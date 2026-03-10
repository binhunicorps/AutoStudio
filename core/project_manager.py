"""
core/project_manager.py
Quản lý lưu/đọc project — auto-naming by date/time, JSON format, importable.
"""

import json
import os
import shutil
from datetime import datetime

DEFAULT_OUTPUT_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")
OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT


def _sanitize_project_id(project_id: str | int | None) -> str:
    """Normalize project id to 4-digit sequence when possible."""
    if project_id is None:
        return ""
    raw = str(project_id).strip()
    if not raw:
        return ""
    if raw.isdigit():
        return f"{int(raw):04d}"
    return raw


def _is_sequential_project_id(project_id: str) -> bool:
    return bool(project_id) and len(project_id) == 4 and project_id.isdigit()


def _next_project_id() -> str:
    """Generate next 4-digit project id based on existing projects."""
    max_id = 0
    if os.path.exists(OUTPUT_ROOT):
        for d in os.listdir(OUTPUT_ROOT):
            proj_path = os.path.join(OUTPUT_ROOT, d)
            json_path = os.path.join(proj_path, "project.json")
            if not (os.path.isdir(proj_path) and os.path.isfile(json_path)):
                continue
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = json.load(f)
                pid = str(data.get("project_id", "")).strip()
                if pid.isdigit():
                    max_id = max(max_id, int(pid))
            except Exception:
                pass
    return f"{max_id + 1:04d}"


def _derive_status(segments_count: int, prompts_count: int, fallback: str = "in_progress") -> str:
    if segments_count > 0 and prompts_count >= segments_count:
        return "done"
    if fallback in ("error", "stopped"):
        return fallback
    return "in_progress"


def set_output_root(path: str):
    """Change OUTPUT_ROOT at runtime (called from settings)."""
    global OUTPUT_ROOT
    candidate = str(path or "").strip()
    if not candidate:
        candidate = DEFAULT_OUTPUT_ROOT
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(candidate)
    try:
        os.makedirs(candidate, exist_ok=True)
        OUTPUT_ROOT = candidate
    except OSError:
        os.makedirs(DEFAULT_OUTPUT_ROOT, exist_ok=True)
        OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT
    return OUTPUT_ROOT


def _auto_project_dir(project_id: str = None) -> tuple[str, str, str]:
    """
    Tạo thư mục project tự động:
      output/DD-MM-YYYY_HH.MM.SS_{project_id}/
    Returns:
        (project_dir, project_name, project_id)
    """
    project_id = _sanitize_project_id(project_id) or _next_project_id()

    now = datetime.now()
    base_name = now.strftime("%d-%m-%Y-%H%M%S")
    folder_name = base_name
    proj_dir = os.path.join(OUTPUT_ROOT, folder_name)
    suffix = 1
    while os.path.exists(proj_dir):
        folder_name = f"{base_name}-{suffix:02d}"
        proj_dir = os.path.join(OUTPUT_ROOT, folder_name)
        suffix += 1
    os.makedirs(proj_dir, exist_ok=False)

    return proj_dir, folder_name, project_id


def get_project_dir_by_id(project_id: str) -> str | None:
    """Tìm thư mục project theo project_id — scan project.json files."""
    target_project_id = _sanitize_project_id(project_id)
    if not target_project_id or not os.path.exists(OUTPUT_ROOT):
        return None
    for d in os.listdir(OUTPUT_ROOT):
        proj_path = os.path.join(OUTPUT_ROOT, d)
        json_path = os.path.join(proj_path, "project.json")
        if os.path.isdir(proj_path) and os.path.isfile(json_path):
            try:
                with open(json_path, encoding="utf-8") as f:
                    data = json.load(f)
                current_id = _sanitize_project_id(data.get("project_id"))
                if current_id and data.get("project_id") != current_id:
                    data["project_id"] = current_id
                    with open(json_path, "w", encoding="utf-8") as wf:
                        json.dump(data, wf, ensure_ascii=False, indent=2)
                if current_id == target_project_id:
                    return proj_path
            except Exception:
                pass
    return None


def save_project(
    topic: str,
    script: str,
    segments: list[dict],
    video_prompts: list[str],
    style_name: str = "",
    video_style_name: str = "",
    model_name: str = "",
    model_video: str = "",
    language: str = "",
    project_id: str = None,
    proj_dir: str = None,
) -> tuple[str, str]:
    """
    Lưu toàn bộ kết quả pipeline vào JSON + files.
    If proj_dir is provided, saves to existing folder.
    Otherwise auto-creates folder.

    Returns:
        (project_dir, project_id)
    """
    if proj_dir and os.path.isdir(proj_dir):
        project_name = os.path.basename(proj_dir)
        if not project_id:
            # Try to read existing project_id
            json_path = os.path.join(proj_dir, "project.json")
            if os.path.isfile(json_path):
                try:
                    with open(json_path, encoding="utf-8") as f:
                        old = json.load(f)
                    project_id = _sanitize_project_id(old.get("project_id")) or _next_project_id()
                except Exception:
                    project_id = _next_project_id()
            else:
                project_id = _next_project_id()
    else:
        proj_dir, project_name, project_id = _auto_project_dir(project_id)

    # 1. Main project JSON (importable)
    segments_count = len(segments)
    prompts_count = len(video_prompts)
    project_data = {
        "project_id": project_id,
        "name": project_name,
        "topic": topic,
        "created_at": datetime.now().isoformat(),
        "style_name": style_name,
        "video_style_name": video_style_name,
        "model": model_name,
        "model_video": model_video,
        "language": language,
        "script": script,
        "segments": segments,
        "video_prompts": video_prompts,
        "segments_count": segments_count,
        "video_prompts_count": prompts_count,
        "script_length": len(script),
        "status": _derive_status(segments_count, prompts_count),
    }
    with open(os.path.join(proj_dir, "project.json"), "w", encoding="utf-8") as f:
        json.dump(project_data, f, ensure_ascii=False, indent=2)

    # 2. Script text (readable)
    with open(os.path.join(proj_dir, "script.txt"), "w", encoding="utf-8") as f:
        f.write(script)

    # 3. Video prompts text (readable, clean)
    with open(os.path.join(proj_dir, "video_prompts.txt"), "w", encoding="utf-8") as f:
        for prompt in video_prompts:
            f.write(f"{prompt}\n")

    return proj_dir, project_id


def create_project_dir(project_id: str = None) -> tuple[str, str, str]:
    """Tạo thư mục project mới — gọi 1 lần ở đầu pipeline. Returns (dir, name, id)."""
    return _auto_project_dir(project_id)


def save_project_incremental(
    proj_dir: str,
    topic: str = "",
    script: str | None = None,
    segments: list[dict] | None = None,
    video_prompts: list[str] | None = None,
    style_name: str = "",
    video_style_name: str = "",
    model_name: str = "",
    model_video: str = "",
    language: str = "",
    project_id: str = None,
    status: str = "",
):
    """
    Cập nhật project.json incrementally — chỉ ghi field có giá trị.
    Dùng sau mỗi step pipeline để tránh mất dữ liệu khi lỗi.
    """
    json_path = os.path.join(proj_dir, "project.json")

    # Load existing or create new
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        generated_id = _sanitize_project_id(project_id) or _next_project_id()
        data = {
            "project_id": generated_id,
            "name": os.path.basename(proj_dir),
            "created_at": datetime.now().isoformat(),
            "status": "in_progress",
        }

    # Update fields that have values
    if topic:
        data["topic"] = topic
    if style_name:
        data["style_name"] = style_name
    if video_style_name:
        data["video_style_name"] = video_style_name
    if model_name:
        data["model"] = model_name
    if model_video:
        data["model_video"] = model_video
    if language:
        data["language"] = language
    if project_id:
        data["project_id"] = _sanitize_project_id(project_id)
    else:
        current_id = _sanitize_project_id(data.get("project_id"))
        if _is_sequential_project_id(current_id):
            data["project_id"] = current_id
        else:
            data["project_id"] = _next_project_id()

    if script is not None:
        data["script"] = script
        data["script_length"] = len(script)
        data["last_step"] = "write"
        # Also save readable script.txt
        with open(os.path.join(proj_dir, "script.txt"), "w", encoding="utf-8") as f:
            f.write(script)

    if segments is not None:
        data["segments"] = segments
        data["segments_count"] = len(segments)
        data["last_step"] = "split"

    if video_prompts is not None:
        data["video_prompts"] = video_prompts
        data["video_prompts_count"] = len(video_prompts)
        data["last_step"] = "video"
        # Also save readable video_prompts.txt
        with open(os.path.join(proj_dir, "video_prompts.txt"), "w", encoding="utf-8") as f:
            for prompt in video_prompts:
                f.write(f"{prompt}\n")

    segs = data.get("segments", [])
    prompts = data.get("video_prompts", [])
    segments_count = len(segs) if isinstance(segs, list) else int(data.get("segments_count", 0) or 0)
    prompts_count = len(prompts) if isinstance(prompts, list) else int(data.get("video_prompts_count", 0) or 0)
    data["segments_count"] = segments_count
    data["video_prompts_count"] = prompts_count
    if status:
        data["status"] = status
    else:
        data["status"] = _derive_status(segments_count, prompts_count, str(data.get("status", "in_progress")))
    data["updated_at"] = datetime.now().isoformat()

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_project(json_path: str) -> dict:
    """
    Import project từ file project.json.

    Returns:
        dict với keys: name, topic, script, segments, video_prompts, ...
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return data


def list_projects() -> list[dict]:
    """Liệt kê tất cả project đã lưu (mới nhất trước)."""
    if not os.path.exists(OUTPUT_ROOT):
        return []
    projects = []
    for proj_dir in os.listdir(OUTPUT_ROOT):
        proj_path = os.path.join(OUTPUT_ROOT, proj_dir)
        json_path = os.path.join(proj_path, "project.json")
        if os.path.isdir(proj_path) and os.path.isfile(json_path):
            try:
                with open(json_path, encoding="utf-8") as f:
                    meta = json.load(f)
                current_id = _sanitize_project_id(meta.get("project_id"))
                if _is_sequential_project_id(current_id):
                    if meta.get("project_id") != current_id:
                        meta["project_id"] = current_id
                else:
                    meta["project_id"] = _next_project_id()
                with open(json_path, "w", encoding="utf-8") as wf:
                    json.dump(meta, wf, ensure_ascii=False, indent=2)
                segs = meta.get("segments", [])
                prompts = meta.get("video_prompts", [])
                segments_count = len(segs) if isinstance(segs, list) else int(meta.get("segments_count", 0) or 0)
                prompts_count = len(prompts) if isinstance(prompts, list) else int(meta.get("video_prompts_count", 0) or 0)
                meta["segments_count"] = segments_count
                meta["video_prompts_count"] = prompts_count
                meta["status"] = _derive_status(
                    segments_count,
                    prompts_count,
                    str(meta.get("status", "in_progress")),
                )
                meta["dir"] = proj_path
                meta["json_path"] = json_path
                projects.append(meta)
            except Exception:
                pass
    # Sort by updated_at or created_at descending
    projects.sort(key=lambda p: p.get("updated_at", p.get("created_at", "")), reverse=True)
    return projects


def delete_project(project_id: str) -> bool:
    """Delete a project by project_id."""
    proj_dir = get_project_dir_by_id(project_id)
    if not proj_dir or not os.path.isdir(proj_dir):
        return False
    shutil.rmtree(proj_dir)
    return True
