"""
server.py — Auto Studio Web App
Flask backend with REST API + SSE for real-time streaming.
"""

import os as _os, sys as _sys
# Add bundled libraries (lib/) to path so no pip install needed
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "lib"))

import json
import os
import threading
import time
import queue
import sys
import subprocess
import shutil
import hashlib
import requests
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime

from flask import Flask, request, jsonify, Response, send_from_directory

from core.ai_splitter import fetch_models, split_content_ai
from core.splitter import split_content, get_summary
from core.content_writer import write_content
from core.video_prompter import generate_video_prompts, generate_video_prompt_single
from core.project_manager import (
    save_project_incremental, load_project,
    list_projects, create_project_dir, get_project_dir_by_id,
    set_output_root, delete_project,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STYLES_PATH = os.path.join(DATA_DIR, "styles.json")
VIDEO_STYLES_PATH = os.path.join(DATA_DIR, "video_styles.json")
DEFAULT_STYLES_PATH = os.path.join(DATA_DIR, "default_styles.json")
DEFAULT_VIDEO_STYLES_PATH = os.path.join(DATA_DIR, "default_video_styles.json")
QUEUE_STATE_PATH = os.path.join(DATA_DIR, "queue_state.json")
P2P_SHARES_PATH = os.path.join(DATA_DIR, "p2p_shares.json")
WEB_DIR = os.path.join(BASE_DIR, "web")
GUILD_PATH = os.path.join(BASE_DIR, "guild.md")


def _init_data_dir():
    """Ensure data/ exists, migrate old config.json, copy default styles."""
    os.makedirs(DATA_DIR, exist_ok=True)
    # Migrate config.json from project root (old location) to data/
    old_config = os.path.join(BASE_DIR, "config.json")
    if os.path.isfile(old_config) and not os.path.isfile(CONFIG_PATH):
        import shutil
        shutil.move(old_config, CONFIG_PATH)
    # Copy default styles to user styles if user hasn't customized yet
    for default_path, user_path in [
        (DEFAULT_STYLES_PATH, STYLES_PATH),
        (DEFAULT_VIDEO_STYLES_PATH, VIDEO_STYLES_PATH),
    ]:
        if os.path.isfile(default_path) and not os.path.isfile(user_path):
            import shutil
            shutil.copy2(default_path, user_path)


_init_data_dir()

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
app.config["JSON_AS_ASCII"] = False
try:
    app.json.ensure_ascii = False
except Exception:
    pass
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

SUPPORTED_LANGUAGES = ("English", "Tiếng Việt", "日本語", "한국어")
_DEFAULT_LANGUAGE = "English"
_LANGUAGE_ALIASES = {
    "english": "English",
    "tiếng anh": "English",
    "en": "English",
    "tiếng việt": "Tiếng Việt",
    "vietnamese": "Tiếng Việt",
    "vi": "Tiếng Việt",
    "日本語": "日本語",
    "tiếng nhật": "日本語",
    "japanese": "日本語",
    "ja": "日本語",
    "한국어": "한국어",
    "tiếng hàn": "한국어",
    "korean": "한국어",
    "ko": "한국어",
}

MODEL_PROBE_CONNECT_TIMEOUT = 5
MODEL_PROBE_READ_TIMEOUT = 12
MODEL_PROBE_RETRIES = 1


def _normalize_language(language: str | None) -> str:
    value = (language or "").strip()
    if not value:
        return _DEFAULT_LANGUAGE
    if value in SUPPORTED_LANGUAGES:
        return value
    return _LANGUAGE_ALIASES.get(value.casefold(), _DEFAULT_LANGUAGE)


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _project_counts(data: dict) -> tuple[int, int]:
    segments = data.get("segments", [])
    prompts = data.get("video_prompts", [])
    segments_count = len(segments) if isinstance(segments, list) else _to_int(data.get("segments_count"), 0)
    prompts_count = len(prompts) if isinstance(prompts, list) else _to_int(data.get("video_prompts_count"), 0)
    return max(0, segments_count), max(0, prompts_count)


def _project_status(data: dict) -> str:
    segments_count, prompts_count = _project_counts(data)
    current = str(data.get("status", "in_progress")).strip().lower()
    if segments_count > 0 and prompts_count >= segments_count:
        return "done"
    if current in ("error", "stopped"):
        return current
    return "in_progress"


def _decorate_project_meta(data: dict) -> dict:
    segments_count, prompts_count = _project_counts(data)
    data["segments_count"] = segments_count
    data["video_prompts_count"] = prompts_count
    data["status"] = _project_status(data)
    data["language"] = _normalize_language(data.get("language"))
    return data


@app.after_request
def _ensure_utf8_charset(resp):
    ctype = resp.headers.get("Content-Type", "")
    lower = ctype.lower()
    if "charset=" in lower:
        return resp
    if (
        lower.startswith("text/")
        or lower.startswith("application/javascript")
        or lower.startswith("application/json")
    ):
        resp.headers["Content-Type"] = f"{ctype}; charset=utf-8" if ctype else "text/plain; charset=utf-8"
    return resp

# ── Global state ──────────────────────────────────────────────────────────────
_log_subscribers: list[queue.Queue] = []
_pipeline_state = {
    "running": False,
    "paused": False,
    "step": "",
    "progress": 0,
    "total": 0,
    "script": "",
    "segments": [],
    "video_prompts": [],
    "proj_dir": "",
    "project_id": "",
    "error": "",
}
_pause_event = threading.Event()
_pause_event.set()
_cancel_flag = False
_last_state_broadcast = 0
_shared_queue: list[dict] = []
_queue_running = False
_queue_progress = {"current": 0, "total": 0, "current_topic": ""}
_state_lock = threading.RLock()
_queue_lock = threading.RLock()
_p2p_lock = threading.RLock()
_translate_cache_lock = threading.RLock()
_translate_cache: OrderedDict[str, str] = OrderedDict()
_TRANSLATE_CACHE_MAX = 256


def _sanitize_queue_item(item: dict) -> dict:
    """Normalize a queue item loaded from API/disk."""
    if not isinstance(item, dict):
        return {}
    clean = dict(item)
    clean["topic"] = str(clean.get("topic", "")).strip()
    clean["style_name"] = str(clean.get("style_name", "") or "")
    clean["video_style_name"] = str(clean.get("video_style_name", "") or "")
    clean["model"] = str(clean.get("model", "") or "")
    clean["model_video"] = str(clean.get("model_video", "") or "")
    clean["language"] = _normalize_language(clean.get("language"))
    return clean


def _load_queue_state() -> list[dict]:
    if not os.path.exists(QUEUE_STATE_PATH):
        return []
    try:
        with open(QUEUE_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if isinstance(data, dict):
        raw_items = data.get("queue", [])
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []

    items: list[dict] = []
    for raw in raw_items:
        clean = _sanitize_queue_item(raw)
        if clean.get("topic"):
            items.append(clean)
    return items


def _save_queue_state_locked():
    """Persist queue to disk. Caller must hold _queue_lock."""
    try:
        os.makedirs(os.path.dirname(QUEUE_STATE_PATH), exist_ok=True)
        payload = {
            "queue": list(_shared_queue),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(QUEUE_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _restore_queue_state():
    global _queue_running
    loaded = _load_queue_state()
    with _queue_lock:
        _shared_queue.clear()
        _shared_queue.extend(loaded)
        _queue_running = False
        _queue_progress["current"] = 0
        _queue_progress["total"] = 0
        _queue_progress["current_topic"] = ""


_p2p_shares: list[dict] = []


def _default_p2p_name() -> str:
    return datetime.now().strftime("Share_%Y%m%d_%H%M%S")


def _sanitize_p2p_token(token: str) -> str:
    t = "".join(ch for ch in str(token or "").upper() if "A" <= ch <= "Z")
    return t[:6]


def _safe_rel_path(rel_path: str, fallback_name: str) -> str:
    rel = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return fallback_name
    rel = os.path.normpath(rel).replace("\\", "/").lstrip("/")
    if rel in ("", ".", "..") or rel.startswith("../") or ":" in rel:
        return fallback_name
    return rel


def _make_p2p_file_entry(path: str, rel_path: str = "") -> dict | None:
    try:
        abs_path = os.path.abspath(str(path or ""))
    except Exception:
        return None
    if not abs_path or not os.path.isfile(abs_path):
        return None
    try:
        size = int(os.path.getsize(abs_path))
        mtime = float(os.path.getmtime(abs_path))
    except Exception:
        size = 0
        mtime = 0.0
    name = os.path.basename(abs_path)
    rel = _safe_rel_path(rel_path, name)
    return {
        "path": abs_path,
        "name": name,
        "rel_path": rel,
        "size": size,
        "mtime": mtime,
    }


def _dedupe_p2p_files(files: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in files:
        path = str(item.get("path", "")).strip().lower()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(item)
    return out


def _normalize_p2p_files(raw_files) -> list[dict]:
    if not isinstance(raw_files, list):
        return []
    files: list[dict] = []
    for raw in raw_files:
        if isinstance(raw, dict):
            entry = _make_p2p_file_entry(raw.get("path", ""), raw.get("rel_path", ""))
        else:
            entry = _make_p2p_file_entry(str(raw), "")
        if entry:
            files.append(entry)
    return _dedupe_p2p_files(files)


def _sanitize_p2p_share(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    token = _sanitize_p2p_token(raw.get("token", ""))
    if len(token) != 6:
        return None
    name = str(raw.get("name", "")).strip() or _default_p2p_name()
    files = _normalize_p2p_files(raw.get("files", []))
    return {
        "token": token,
        "name": name,
        "files": files,
        "created_at": str(raw.get("created_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
        "updated_at": str(raw.get("updated_at", "")).strip() or datetime.now().isoformat(timespec="seconds"),
        "download_count": max(0, int(raw.get("download_count", 0) or 0)),
        "last_download_at": str(raw.get("last_download_at", "")).strip(),
        "last_download_dir": str(raw.get("last_download_dir", "")).strip(),
    }


def _load_p2p_shares() -> list[dict]:
    if not os.path.exists(P2P_SHARES_PATH):
        return []
    try:
        with open(P2P_SHARES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        raw_items = data.get("shares", [])
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []
    out: list[dict] = []
    for raw in raw_items:
        clean = _sanitize_p2p_share(raw)
        if clean:
            out.append(clean)
    return out


def _save_p2p_shares_locked():
    try:
        os.makedirs(os.path.dirname(P2P_SHARES_PATH), exist_ok=True)
        payload = {
            "shares": list(_p2p_shares),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(P2P_SHARES_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _restore_p2p_state():
    loaded = _load_p2p_shares()
    with _p2p_lock:
        _p2p_shares.clear()
        _p2p_shares.extend(loaded)


def _collect_folder_files(folder_path: str) -> list[dict]:
    abs_folder = os.path.abspath(str(folder_path or ""))
    if not abs_folder or not os.path.isdir(abs_folder):
        return []
    files: list[dict] = []
    for root, _dirs, names in os.walk(abs_folder):
        names.sort()
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, abs_folder).replace("\\", "/")
            entry = _make_p2p_file_entry(full, rel)
            if entry:
                files.append(entry)
    return _dedupe_p2p_files(files)


def _safe_dir_name(name: str, fallback: str = "share") -> str:
    raw = str(name or "").strip()
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in raw)
    cleaned = cleaned.strip().strip(".")
    cleaned = cleaned[:120].rstrip(" .")
    return cleaned or fallback


def _path_is_within_dir(path: str, parent_dir: str) -> bool:
    try:
        abs_path = os.path.abspath(str(path or ""))
        abs_parent = os.path.abspath(str(parent_dir or ""))
        return bool(abs_path and abs_parent and os.path.commonpath([abs_path, abs_parent]) == abs_parent)
    except Exception:
        return False


def _unique_dir_path(parent_dir: str, base_name: str) -> str:
    candidate = os.path.join(parent_dir, base_name)
    if not os.path.exists(candidate):
        return candidate
    idx = 2
    while True:
        candidate = os.path.join(parent_dir, f"{base_name} ({idx})")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _unique_rel_path(rel_path: str, used_paths: set[str]) -> str:
    candidate = str(rel_path or "").replace("\\", "/")
    key = candidate.lower()
    if key not in used_paths:
        used_paths.add(key)
        return candidate
    root_name, ext = os.path.splitext(candidate)
    idx = 2
    while True:
        candidate = f"{root_name} ({idx}){ext}"
        key = candidate.lower()
        if key not in used_paths:
            used_paths.add(key)
            return candidate
        idx += 1


def _new_p2p_token(existing_tokens: set[str]) -> str:
    alphabet = string.ascii_uppercase
    for _ in range(2000):
        token = "".join(random.choice(alphabet) for _ in range(6))
        if token not in existing_tokens:
            return token
    raise RuntimeError("Unable to generate unique token")


def _find_p2p_share_locked(token: str) -> dict | None:
    t = _sanitize_p2p_token(token)
    for share in _p2p_shares:
        if share.get("token") == t:
            return share
    return None


def _p2p_share_summary(share: dict, include_files: bool = True, include_paths: bool = False) -> dict:
    files = list(share.get("files", []) or [])
    file_count = len(files)
    total_size = sum(max(0, int(f.get("size", 0) or 0)) for f in files)
    last_download_dir = str(share.get("last_download_dir", "")).strip()
    out = {
        "token": share.get("token", ""),
        "name": share.get("name", ""),
        "created_at": share.get("created_at", ""),
        "updated_at": share.get("updated_at", ""),
        "download_count": max(0, int(share.get("download_count", 0) or 0)),
        "last_download_at": share.get("last_download_at", ""),
        "last_download_dir": last_download_dir,
        "has_local_download": bool(last_download_dir),
        "download_dir_exists": bool(last_download_dir and os.path.isdir(last_download_dir)),
        "file_count": file_count,
        "total_size": total_size,
    }
    if include_files:
        norm_files = []
        for f in files:
            item = {
                "name": f.get("name", ""),
                "rel_path": f.get("rel_path", ""),
                "size": max(0, int(f.get("size", 0) or 0)),
                "mtime": float(f.get("mtime", 0) or 0),
            }
            if include_paths:
                item["path"] = f.get("path", "")
            norm_files.append(item)
        out["files"] = norm_files
    return out


def _load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_config_file(data: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_dir_path(path: str, fallback: str) -> str:
    candidate = str(path or "").strip() or fallback
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(candidate)
    try:
        os.makedirs(candidate, exist_ok=True)
        return candidate
    except OSError:
        safe_fallback = fallback
        if not os.path.isabs(safe_fallback):
            safe_fallback = os.path.abspath(safe_fallback)
        os.makedirs(safe_fallback, exist_ok=True)
        return safe_fallback


def _normalize_config_paths(cfg: dict | None = None, persist: bool = False) -> dict:
    current = dict(cfg) if isinstance(cfg, dict) else _load_config()
    output_dir = _normalize_dir_path(current.get("output_dir", ""), os.path.join(BASE_DIR, "output"))
    current["output_dir"] = set_output_root(output_dir)
    current["p2p_download_dir"] = _normalize_dir_path(
        current.get("p2p_download_dir", ""),
        os.path.join(current["output_dir"], "P2P_Downloads"),
    )
    if persist:
        _write_config_file(current)
    return current


def _save_config(updates: dict):
    current = _load_config()
    current.update(updates)
    current = _normalize_config_paths(current)
    _write_config_file(current)
    return current


def _default_p2p_download_dir(cfg: dict | None = None) -> str:
    current = dict(cfg) if isinstance(cfg, dict) else _normalize_config_paths()
    output_dir = str(current.get("output_dir", "")).strip()
    root_dir = output_dir or os.path.join(BASE_DIR, "output")
    return os.path.join(root_dir, "P2P_Downloads")


def _get_p2p_download_dir(cfg: dict | None = None) -> str:
    current = dict(cfg) if isinstance(cfg, dict) else _normalize_config_paths()
    configured = str(current.get("p2p_download_dir", "")).strip()
    return configured or _default_p2p_download_dir(current)


def _public_config(cfg: dict) -> dict:
    """Return config safe for frontend (without exposing secrets)."""
    safe = dict(cfg)
    safe["has_api_key"] = bool(cfg.get("api_key"))
    safe["api_key"] = ""
    if "direct_api_key" in safe:
        safe["has_direct_api_key"] = bool(cfg.get("direct_api_key"))
        safe["direct_api_key"] = ""
    return safe


def _load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else []


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_picker_initial_dir(path: str) -> str:
    raw = str(path or "").strip()
    home = os.path.expanduser("~")
    if not raw:
        return home
    try:
        candidate = os.path.abspath(raw)
    except Exception:
        return home
    return candidate if os.path.isdir(candidate) else home


# ── Native Win32 file/folder pickers ─────────────────────────────────────────
# Uses IFileOpenDialog COM via a subprocess helper script for modern Explorer UI.
# Both file & folder pickers share the same dialog style. Fast (~0.3s startup).
_WIN_DIALOG_HELPER = os.path.join(BASE_DIR, "scripts", "_win_dialog.py")


def _run_win_dialog(mode: str, initial_dir: str, title: str) -> tuple[list[str], str | None]:
    """Run the helper script to show a native file/folder picker dialog.
    Returns (list_of_paths, error_string_or_None).
    """
    safe_initial = _normalize_picker_initial_dir(initial_dir)
    cmd = [sys.executable, _WIN_DIALOG_HELPER, mode, safe_initial, title or ""]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as e:
        return [], str(e)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        return [], err or None
    paths = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return paths, None


def _pick_files_native(initial_dir: str, title: str) -> tuple[list[str], str | None]:
    """Open native multi-file picker (modern Explorer UI)."""
    return _run_win_dialog("files", initial_dir, title)


def _pick_folder_native(initial_dir: str, title: str) -> tuple[str, str | None]:
    """Open native folder picker (same modern Explorer UI as file picker)."""
    paths, err = _run_win_dialog("folder", initial_dir, title)
    if err:
        return "", err
    return paths[0] if paths else "", None


def _extract_chat_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts).strip()
    return str(content).strip()


def _translate_cache_key(model_name: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{model_name}|{digest}"


def _translate_cache_get(model_name: str, text: str) -> str | None:
    key = _translate_cache_key(model_name, text)
    with _translate_cache_lock:
        if key not in _translate_cache:
            return None
        value = _translate_cache.pop(key)
        _translate_cache[key] = value
        return value


def _translate_cache_set(model_name: str, text: str, translated: str):
    key = _translate_cache_key(model_name, text)
    with _translate_cache_lock:
        if key in _translate_cache:
            _translate_cache.pop(key)
        _translate_cache[key] = translated
        while len(_translate_cache) > _TRANSLATE_CACHE_MAX:
            _translate_cache.popitem(last=False)


def _is_likely_vietnamese(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    if any(ch in lower for ch in "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"):
        return True
    common = (" và ", " của ", " là ", " không ", " trong ", " cho ", " với ", " được ", " một ")
    hits = sum(1 for token in common if token in f" {lower} ")
    return hits >= 2


def _pick_translate_model(cfg: dict, model_hint: str = "", mode: str = "fast") -> str:
    if mode in ("fixed", "force", "locked") and str(model_hint or "").strip():
        return str(model_hint).strip()

    configured = str(cfg.get("model_translate", "")).strip()
    if configured:
        return configured

    available = []
    for m in cfg.get("available_models", []):
        ms = str(m).strip()
        if ms:
            available.append(ms)

    fallback = (
        model_hint
        or str(cfg.get("model_video", "")).strip()
        or str(cfg.get("model", "")).strip()
        or (available[0] if available else "")
    )
    if not fallback:
        return ""
    if mode != "fast":
        return fallback

    candidates = list(available)
    if fallback not in candidates:
        candidates.append(fallback)

    fast_tags = ("flash", "mini", "haiku", "nano", "lite", "small", "fast", "turbo", "8b", "7b")

    def _score(name: str) -> tuple[int, int]:
        n = name.lower()
        for i, tag in enumerate(fast_tags):
            if tag in n:
                return (i, len(n))
        if "gpt-5" in n and "mini" not in n and "nano" not in n:
            return (90, len(n))
        return (50, len(n))

    candidates.sort(key=_score)
    return candidates[0]


def _translate_text_to_vi(text: str, model_name: str, endpoint: str, api_key: str) -> tuple[str, str]:
    base = endpoint.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    url = f"{base}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Dịch văn bản sang tiếng Việt tự nhiên, chính xác. "
                    "Giữ nguyên ý nghĩa, giọng điệu và xuống dòng. "
                    "Chỉ trả về bản dịch, không thêm giải thích."
                ),
            },
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "stream": False,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=(15, 120))
    resp.raise_for_status()
    data = resp.json()
    translated = _extract_chat_text(data)
    if not translated:
        raise RuntimeError("Empty translation response")
    return translated, base


def _broadcast(data_dict: dict):
    """Send data dict to all SSE subscribers."""
    data = json.dumps(data_dict, ensure_ascii=False)
    dead = []
    for q in _log_subscribers:
        try:
            q.put_nowait(data)
        except Exception:
            dead.append(q)
    for q in dead:
        if q in _log_subscribers:
            _log_subscribers.remove(q)


def _broadcast_log(msg: str):
    msg = _normalize_log_text(msg)
    ts = datetime.now().strftime("%H:%M:%S")
    _broadcast({"type": "log", "time": ts, "message": msg})


def _api_base(endpoint: str) -> str:
    base = str(endpoint or "").rstrip("/")
    if base and not base.endswith("/v1"):
        base += "/v1"
    return base


def _api_headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key and str(api_key).strip():
        headers["Authorization"] = f"Bearer {str(api_key).strip()}"
    return headers


def _probe_single_model(base: str, headers: dict, model_name: str) -> tuple[bool, str]:
    """
    Probe one model with a tiny completion request.
    Returns:
      (True, "ok") when model responds HTTP 200
      (False, reason) otherwise, reason in: timeout/http_xxx/error_name
    """
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    url = f"{base}/chat/completions"
    last_reason = "timeout"
    for _ in range(max(1, MODEL_PROBE_RETRIES)):
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=(MODEL_PROBE_CONNECT_TIMEOUT, MODEL_PROBE_READ_TIMEOUT),
            )
            if resp.status_code == 200:
                return True, "ok"
            last_reason = f"http_{resp.status_code}"
            if resp.status_code in (400, 401, 403, 404):
                break
        except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout):
            last_reason = "timeout"
        except requests.exceptions.RequestException as ex:
            last_reason = ex.__class__.__name__
            break
    return False, last_reason


def _probe_models_ready(endpoint: str, api_key: str, models: list[str], log_prefix: str = "") -> tuple[list[str], dict]:
    """
    Probe model readiness in parallel to avoid long sequential waits.
    Fallback rule:
      - if every probe times out, use listed models as available (unverified)
    """
    if not models:
        return [], {"timeouts": 0, "fallback_used": False}

    base = _api_base(endpoint)
    headers = _api_headers(api_key)
    max_workers = min(8, max(1, len(models)))
    results: dict[str, tuple[bool, str]] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(_probe_single_model, base, headers, model): model for model in models}
        for fut in as_completed(fut_map):
            model = fut_map[fut]
            try:
                ok, reason = fut.result()
            except Exception as ex_err:
                ok, reason = False, ex_err.__class__.__name__
            results[model] = (ok, reason)

    ready: list[str] = []
    timeout_count = 0
    for model in models:
        ok, reason = results.get(model, (False, "unknown"))
        if ok:
            ready.append(model)
            _broadcast_log(f"{log_prefix}  [ok] {model}")
            continue
        if reason == "timeout":
            timeout_count += 1
        _broadcast_log(f"{log_prefix}  [fail] {model} ({reason})")

    fallback_used = False
    if not ready and timeout_count == len(models):
        fallback_used = True
        ready = list(models)
        _broadcast_log(f"{log_prefix}all probes timed out; using listed models as available")

    return ready, {"timeouts": timeout_count, "fallback_used": fallback_used}


_MOJIBAKE_TOKENS = (
    "\u00C3",      # Ã
    "\u00E2",      # â
    "\u00E1\u00BB",  # á»
    "\u00E1\u00BA",  # áº
    "\u00F0\u0178",  # ðŸ
    "\u00E2\u0153",  # âœ
    "\u00E2\u20AC",  # â€
    "\u00C2",      # Â
)


def _mojibake_score(text: str) -> int:
    if not text:
        return 0
    score = text.count("\uFFFD") * 4
    for token in _MOJIBAKE_TOKENS:
        score += text.count(token)
    return score


def _repair_mojibake(text: str) -> str:
    current = text
    current_score = _mojibake_score(current)
    for enc in ("cp1252", "latin-1"):
        try:
            fixed = current.encode(enc).decode("utf-8")
        except Exception:
            continue
        fixed_score = _mojibake_score(fixed)
        if fixed and "\uFFFD" not in fixed and fixed_score < current_score:
            return fixed
    return current


def _normalize_log_text(msg: str) -> str:
    """Best-effort fix for mojibake logs without altering valid UTF-8 text."""
    if not isinstance(msg, str):
        msg = str(msg)
    if _mojibake_score(msg) <= 0:
        return msg
    return _repair_mojibake(msg)


def _broadcast_state(force: bool = False):
    """Broadcast pipeline state. Throttled to max 4Hz unless force=True."""
    global _last_state_broadcast
    now = time.time()
    if not force and (now - _last_state_broadcast) < 0.25:
        return
    _last_state_broadcast = now
    with _state_lock:
        state = deepcopy(_pipeline_state)
    _broadcast({"type": "state", **state})


def _broadcast_script_chunk(chunk: str):
    """Send incremental script chunk (lightweight, no full state)."""
    _broadcast({"type": "script_chunk", "chunk": chunk})


def _segment_tolerance(target_seconds: float) -> float:
    """Allowed deviation from target reading duration."""
    try:
        target = float(target_seconds)
    except Exception:
        target = 8.0
    return max(1.5, round(target * 0.35, 1))


def _segment_duration(words: int, wpm: int) -> float:
    safe_wpm = max(int(wpm or 130), 1)
    return round((max(words, 0) / safe_wpm) * 60.0, 1)


def _split_segments_from_script(script: str, wpm: int, target_seconds: float) -> tuple[list[dict], dict]:
    """
    Segment validator used by pipeline:
    - primary mode: one segment per non-empty line (AI already writes line-by-line ~8s)
    - fallback: deterministic sentence splitter when script has no usable line breaks
    """
    text = (script or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n") if line.strip()]
    tolerance = _segment_tolerance(target_seconds)

    mode = "line_break"
    units = lines
    if len(units) < 2:
        mode = "sentence_fallback"
        split_result = split_content(text, wpm=wpm, target_seconds=target_seconds)
        units = [str(s.get("text", "")).strip() for s in split_result if str(s.get("text", "")).strip()]

    segments = []
    for i, unit in enumerate(units, start=1):
        words = len(unit.split())
        duration = _segment_duration(words, wpm)
        delta = round(duration - float(target_seconds), 1)
        segments.append({
            "index": i,
            "text": unit,
            "words": words,
            "duration": duration,
            "delta": delta,
            "in_range": abs(delta) <= tolerance,
        })

    meta = {
        "mode": mode,
        "source_count": len(units),
        "tolerance": tolerance,
    }
    return segments, meta


def _log_segment_validation(segments: list[dict], wpm: int, target_seconds: float, meta: dict, prefix: str):
    tolerance = meta.get("tolerance", _segment_tolerance(target_seconds))
    _broadcast_log(
        f"{prefix} validate mode={meta.get('mode', 'unknown')} "
        f"wpm={wpm} target={target_seconds}s tolerance=±{tolerance}s count={len(segments)}"
    )
    out_of_range = 0
    for seg in segments:
        seg_id = seg.get("index")
        words = seg.get("words", 0)
        duration = seg.get("duration", 0)
        delta = seg.get("delta", 0)
        in_range = bool(seg.get("in_range", False))
        if not in_range:
            out_of_range += 1
        status = "ok" if in_range else "check"
        _broadcast_log(
            f"{prefix} segment_id={seg_id} words={words} duration={duration}s "
            f"delta={delta:+.1f}s status={status}"
        )
    _broadcast_log(f"{prefix} out_of_range={out_of_range}/{len(segments)}")


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Static
# ══════════════════════════════════════════════════════════════════════════════

# Init output dir from config at import time
_init_cfg = _normalize_config_paths(persist=True)
_restore_queue_state()
_restore_p2p_state()


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/api/guide", methods=["GET"])
def get_guide():
    if not os.path.isfile(GUILD_PATH):
        return jsonify({"error": "guild.md not found"}), 404
    try:
        with open(GUILD_PATH, encoding="utf-8") as f:
            content = f.read()
        updated_at = datetime.fromtimestamp(os.path.getmtime(GUILD_PATH)).isoformat(timespec="seconds")
        return jsonify({
            "path": "guild.md",
            "updated_at": updated_at,
            "content": content,
        })
    except Exception as e:
        return jsonify({"error": f"Cannot read guild.md: {e}"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Config
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(_public_config(_load_config()))


@app.route("/api/config", methods=["POST"])
def post_config():
    updates = request.get_json(force=True) or {}

    # Keep existing key if frontend submits an empty value.
    if updates.get("api_key", None) == "":
        updates.pop("api_key")
    if updates.get("direct_api_key", None) == "":
        updates.pop("direct_api_key")

    cfg = _save_config(updates)
    return jsonify(_public_config(cfg))


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Models
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/models", methods=["GET"])
def get_models():
    cfg = _load_config()
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    if not endpoint:
        return jsonify({"error": "No endpoint configured", "models": [], "ready": []}), 400

    try:
        models = fetch_models(endpoint, api_key)
    except Exception as e:
        return jsonify({"error": str(e), "models": [], "ready": []}), 500

    _broadcast_log(f"{len(models)} models found")
    ready, probe_meta = _probe_models_ready(endpoint, api_key, models, log_prefix="[models]")
    if probe_meta.get("fallback_used"):
        _broadcast_log("[models] readiness check unavailable; fallback to listed models")
    _broadcast_log(f"{len(ready)}/{len(models)} models ready")
    _save_config({"available_models": models, "ready_models": ready})
    return jsonify({"models": models, "ready": ready})


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Styles
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/styles", methods=["GET"])
def get_styles():
    return jsonify({
        "content": _load_json(STYLES_PATH),
        "video": _load_json(VIDEO_STYLES_PATH),
    })


@app.route("/api/styles/<section>", methods=["POST"])
def post_style(section):
    if section not in ("content", "video"):
        return jsonify({"error": f"Invalid section: {section}"}), 400

    data = request.get_json(force=True)
    action = data.get("action", "add")
    path = STYLES_PATH if section == "content" else VIDEO_STYLES_PATH
    styles = _load_json(path)

    if action == "add":
        styles.append(data.get("item", {}))
    elif action == "edit":
        idx = data.get("index", -1)
        if 0 <= idx < len(styles):
            styles[idx] = data.get("item", {})
    elif action == "delete":
        idx = data.get("index", -1)
        if 0 <= idx < len(styles):
            styles.pop(idx)

    _save_json(path, styles)
    return jsonify(styles)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — SSE Log Stream
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/events")
def sse_events():
    q = queue.Queue(maxsize=1000)
    _log_subscribers.append(q)

    # Send current pipeline state immediately
    with _state_lock:
        state = deepcopy(_pipeline_state)
    q.put(json.dumps({"type": "state", **state}, ensure_ascii=False))

    def stream():
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except GeneratorExit:
            pass
        finally:
            if q in _log_subscribers:
                _log_subscribers.remove(q)

    return Response(
        stream(), mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"}
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Pipeline
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/pipeline/start", methods=["POST"])
def pipeline_start():
    global _cancel_flag
    with _state_lock:
        if _pipeline_state["running"]:
            return jsonify({"error": "Pipeline already running"}), 409
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue is running"}), 409

    params = request.get_json(force=True)
    _cancel_flag = False
    _pause_event.set()

    t = threading.Thread(target=_run_pipeline, args=(params,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/pipeline/pause", methods=["POST"])
def pipeline_pause():
    with _state_lock:
        if not _pipeline_state["running"]:
            return jsonify({"error": "Not running"}), 400
    if _pause_event.is_set():
        _pause_event.clear()
        with _state_lock:
            _pipeline_state["paused"] = True
        _broadcast_log("[pipeline][pause] paused_by_user")
    else:
        _pause_event.set()
        with _state_lock:
            _pipeline_state["paused"] = False
        _broadcast_log("[pipeline][pause] resumed_by_user")
    _broadcast_state(force=True)
    with _state_lock:
        paused = _pipeline_state["paused"]
    return jsonify({"paused": paused})


@app.route("/api/pipeline/stop", methods=["POST"])
def pipeline_stop():
    global _cancel_flag
    _cancel_flag = True
    _pause_event.set()
    _broadcast_log("[pipeline][stop] stop_requested_by_user")
    return jsonify({"status": "stopped"})


@app.route("/api/pipeline/state", methods=["GET"])
def pipeline_state_route():
    with _state_lock:
        return jsonify(deepcopy(_pipeline_state))


def _run_pipeline(params: dict):
    global _cancel_flag

    cfg = _load_config()
    topic = params.get("topic", "")
    style_name = params.get("style_name", "")
    language = _normalize_language(params.get("language"))
    model_script = params.get("model", cfg.get("model", ""))
    model_video = params.get("model_video", cfg.get("model_video", model_script))
    video_style_name = params.get("video_style_name", "")
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    wpm = cfg.get("wpm", 130)
    target_seconds = cfg.get("target_seconds", 8.0)

    # Resolve styles
    styles = _load_json(STYLES_PATH)
    style = next((s for s in styles if s.get("name") == style_name), styles[0] if styles else {"name": "General", "prompt": ""})
    duration = style.get("duration_minutes", 0)

    video_styles = _load_json(VIDEO_STYLES_PATH)
    vstyle = next((s for s in video_styles if s.get("name") == video_style_name), None)
    video_style_prompt = vstyle.get("prompt", "") if vstyle else ""

    requested_project_id = str(params.get("project_id", "")).strip()
    proj_dir = get_project_dir_by_id(requested_project_id) if requested_project_id else None
    if proj_dir:
        project_id = requested_project_id
        proj_name = os.path.basename(proj_dir)
    else:
        proj_dir, proj_name, project_id = create_project_dir()

    with _state_lock:
        _pipeline_state.update({
            "running": True, "paused": False, "step": "write",
            "progress": 0, "total": 3, "error": "",
            "script": "", "segments": [], "video_prompts": [],
            "proj_dir": proj_dir, "project_id": project_id,
        })
    _broadcast_state(force=True)
    _broadcast_log(
        f"[pipeline][start] topic={topic} project_id={project_id} project_name={proj_name} "
        f"language={language} content_style={style_name or style.get('name', 'General')} "
        f"video_style={video_style_name or '(none)'} model_content={model_script} model_video={model_video}"
    )
    save_project_incremental(
        proj_dir,
        topic=topic,
        style_name=style_name,
        video_style_name=video_style_name,
        model_name=model_script,
        model_video=model_video,
        language=language,
        project_id=project_id,
        status="in_progress",
    )

    def _check_cancel_pause():
        """Check cancel flag and wait if paused. Call after each API call."""
        if _cancel_flag:
            raise InterruptedError("Cancelled")
        _pause_event.wait()
        if _cancel_flag:
            raise InterruptedError("Cancelled")

    try:
        # ── Step 1: Write ──────────────────────────────────────────────────
        _check_cancel_pause()

        _broadcast_log(f"[step 1/3][write] start project_id={project_id} topic={topic}")
        with _state_lock:
            _pipeline_state["step"] = "write"
        _broadcast_state(force=True)

        def on_token(t):
            with _state_lock:
                _pipeline_state["script"] += t
            _broadcast_script_chunk(t)

        _broadcast_log(
            f"[api][write] request=POST /v1/chat/completions "
            f"model={model_script} style={style_name or style.get('name', 'General')} language={language}"
        )
        script = write_content(
            title=topic, style=style, model_name=model_script,
            endpoint=endpoint, api_key=api_key,
            wpm=wpm, target_sec=target_seconds,
            duration_minutes=duration, language=language,
            on_token=on_token, log_fn=_broadcast_log,
            cancel_check=lambda: _cancel_flag,
        )

        _check_cancel_pause()

        with _state_lock:
            _pipeline_state["script"] = script
            _pipeline_state["progress"] = 1
        _broadcast_state(force=True)

        save_project_incremental(
            proj_dir, topic=topic, script=script,
            style_name=style_name, video_style_name=video_style_name,
            model_name=model_script, model_video=model_video,
            language=language, project_id=project_id,
        )
        _broadcast_log(f"[step 1/3][write] done project_id={project_id} chars={len(script)}")

        # ── Step 2: Split ──────────────────────────────────────────────────
        _check_cancel_pause()

        _broadcast_log(f"[step 2/3][split] start project_id={project_id} script_chars={len(script)}")
        with _state_lock:
            _pipeline_state["step"] = "split"
        _broadcast_state(force=True)

        segments, seg_meta = _split_segments_from_script(
            script, wpm=wpm, target_seconds=target_seconds
        )
        _log_segment_validation(
            segments, wpm=wpm, target_seconds=target_seconds,
            meta=seg_meta, prefix="[segments][pipeline]"
        )

        _check_cancel_pause()

        with _state_lock:
            _pipeline_state["segments"] = segments
            _pipeline_state["progress"] = 2
        _broadcast_state(force=True)

        save_project_incremental(
            proj_dir, segments=segments, project_id=project_id,
            topic=topic, style_name=style_name, video_style_name=video_style_name,
            model_name=model_script, model_video=model_video, language=language,
        )
        _broadcast_log(
            f"[step 2/3][split] done project_id={project_id} segments={len(segments)} "
            f"mode={seg_meta.get('mode')} target_seconds={target_seconds}"
        )

        # ── Step 3: Video Prompts ──────────────────────────────────────────
        _check_cancel_pause()

        _broadcast_log(f"[step 3/3][video] start project_id={project_id} segments={len(segments)}")
        with _state_lock:
            _pipeline_state["step"] = "video"
        _broadcast_state(force=True)

        def on_prompt_saved(prompts_list):
            with _state_lock:
                _pipeline_state["video_prompts"] = list(prompts_list)
            _broadcast_state()
            save_project_incremental(
                proj_dir, video_prompts=list(prompts_list), project_id=project_id,
                topic=topic, style_name=style_name, video_style_name=video_style_name,
                model_name=model_script, model_video=model_video, language=language,
            )

        def on_progress(done, total, prompt):
            _broadcast_state()
            # Check pause/cancel between each video prompt
            _check_cancel_pause()

        _broadcast_log(
            f"[api][video] start model={model_video} style={video_style_name or '(none)'} "
            f"segments={len(segments)} endpoint={endpoint.rstrip('/')}"
        )
        prompts = generate_video_prompts(
            segments, video_style=video_style_prompt,
            model_name=model_video, endpoint=endpoint, api_key=api_key,
            progress_fn=on_progress, log_fn=_broadcast_log,
            cancel_check=lambda: _cancel_flag,
            on_prompt_saved=on_prompt_saved,
        )
        with _state_lock:
            _pipeline_state["video_prompts"] = prompts
            _pipeline_state["progress"] = 3
            _pipeline_state["step"] = "done"
        _broadcast_state(force=True)

        save_project_incremental(
            proj_dir, video_prompts=prompts,
            style_name=style_name, video_style_name=video_style_name,
            model_name=model_script, model_video=model_video,
            language=language, project_id=project_id,
        )
        _broadcast_log(
            f"[pipeline][done] project_id={project_id} segments={len(segments)} prompts={len(prompts)} "
            f"topic={topic}"
        )

    except InterruptedError:
        with _state_lock:
            _pipeline_state["step"] = "stopped"
        save_project_incremental(proj_dir, project_id=project_id, status="stopped")
        _broadcast_log(f"[pipeline][stop] stopped_by_user project_id={project_id}")
    except Exception as e:
        with _state_lock:
            _pipeline_state["step"] = "error"
            _pipeline_state["error"] = str(e)
        save_project_incremental(proj_dir, project_id=project_id, status="error")
        _broadcast_log(f"[pipeline][error] project_id={project_id} detail={e}")
    finally:
        with _state_lock:
            _pipeline_state["running"] = False
            _pipeline_state["paused"] = False
        _broadcast_state(force=True)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Projects
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/projects", methods=["GET"])
def get_projects():
    projects = list_projects()
    for p in projects:
        _decorate_project_meta(p)
        p.pop("script", None)
        p.pop("segments", None)
        p.pop("video_prompts", None)
    return jsonify(projects)


@app.route("/api/projects/<path:pid>", methods=["GET"])
def get_project(pid):
    proj_dir = get_project_dir_by_id(pid)
    if not proj_dir:
        return jsonify({"error": "Project not found"}), 404
    data = load_project(os.path.join(proj_dir, "project.json"))
    _decorate_project_meta(data)
    return jsonify(data)


@app.route("/api/projects/<path:pid>/update", methods=["POST"])
def update_project(pid):
    proj_dir = get_project_dir_by_id(pid)
    if not proj_dir:
        return jsonify({"error": "Project not found"}), 404

    body = request.get_json(force=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid payload"}), 400

    topic = str(body.get("topic", "") or "")
    style_name = str(body.get("style_name", "") or "")
    video_style_name = str(body.get("video_style_name", "") or "")
    model_name = str(body.get("model", "") or "")
    model_video = str(body.get("model_video", "") or "")
    language = _normalize_language(body.get("language"))

    script = body.get("script") if "script" in body else None
    segments = body.get("segments") if "segments" in body else None
    video_prompts = body.get("video_prompts") if "video_prompts" in body else None

    if script is not None:
        script = str(script).replace("\r\n", "\n").replace("\r", "\n")

    if segments is not None:
        if not isinstance(segments, list):
            return jsonify({"error": "segments must be a list"}), 400
        cleaned_segments = []
        for i, seg in enumerate(segments, start=1):
            if isinstance(seg, dict):
                item = dict(seg)
                item["index"] = int(item.get("index") or i)
                item["text"] = str(item.get("text", "")).strip()
            else:
                item = {"index": i, "text": str(seg).strip()}
            if item["text"]:
                cleaned_segments.append(item)
        segments = cleaned_segments

    if video_prompts is not None:
        if not isinstance(video_prompts, list):
            return jsonify({"error": "video_prompts must be a list"}), 400
        video_prompts = [str(x) for x in video_prompts]

    save_project_incremental(
        proj_dir,
        topic=topic,
        script=script,
        segments=segments,
        video_prompts=video_prompts,
        style_name=style_name,
        video_style_name=video_style_name,
        model_name=model_name,
        model_video=model_video,
        language=language,
        project_id=pid,
    )

    data = load_project(os.path.join(proj_dir, "project.json"))
    _decorate_project_meta(data)
    _broadcast_log(
        f"[project][update] project_id={data.get('project_id', pid)} "
        f"script_chars={len(data.get('script', '') or '')} "
        f"segments={len(data.get('segments', []) or [])} "
        f"video_prompts={len(data.get('video_prompts', []) or [])}"
    )
    return jsonify(data)


@app.route("/api/projects/<path:pid>", methods=["DELETE"])
def delete_project_route(pid):
    try:
        ok = delete_project(pid)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not ok:
        return jsonify({"error": "Project not found"}), 404
    return jsonify({"status": "deleted", "project_id": pid})


@app.route("/api/projects/import", methods=["POST"])
def import_project():
    if "file" in request.files:
        f = request.files["file"]
        data = json.load(f)
    else:
        data = request.get_json(force=True)
    return jsonify(data)


@app.route("/api/projects/<path:pid>/export", methods=["GET"])
def export_project(pid):
    proj_dir = get_project_dir_by_id(pid)
    if not proj_dir:
        return jsonify({"error": "Project not found"}), 404
    data = load_project(os.path.join(proj_dir, "project.json"))
    _decorate_project_meta(data)
    resp = Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{pid}.json"'},
    )
    return resp


def _open_in_file_manager(path: str):
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


@app.route("/api/projects/<path:pid>/open-folder", methods=["POST"])
def open_project_folder(pid):
    proj_dir = get_project_dir_by_id(pid)
    if not proj_dir:
        return jsonify({"error": "Project not found"}), 404
    try:
        _open_in_file_manager(proj_dir)
        return jsonify({"status": "opened", "path": proj_dir})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Splitter
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/split/manual", methods=["POST"])
def split_manual():
    body = request.get_json(force=True)
    text = body.get("text", "")
    wpm = body.get("wpm", 130)
    target_seconds = body.get("target_seconds", 8.0)
    if not text.strip():
        return jsonify({"error": "No text provided"}), 400
    segments, seg_meta = _split_segments_from_script(text, wpm=wpm, target_seconds=target_seconds)
    _log_segment_validation(
        segments, wpm=wpm, target_seconds=target_seconds,
        meta=seg_meta, prefix="[segments][manual]"
    )
    summary = get_summary(segments)
    summary["mode"] = seg_meta.get("mode")
    summary["tolerance"] = seg_meta.get("tolerance")
    summary["out_of_range"] = sum(1 for s in segments if not s.get("in_range"))
    _broadcast_log(
        f"[splitter] manual split count={summary['count']} mode={summary['mode']} "
        f"out_of_range={summary['out_of_range']}"
    )
    return jsonify({"segments": segments, "summary": summary})


@app.route("/api/split/ai", methods=["POST"])
def split_ai():
    body = request.get_json(force=True)
    text = body.get("text", "")
    cfg = _load_config()
    model = body.get("model", cfg.get("model", ""))
    wpm = body.get("wpm", cfg.get("wpm", 130))
    target_seconds = body.get("target_seconds", cfg.get("target_seconds", 8.0))
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    if not text.strip():
        return jsonify({"error": "No text provided"}), 400
    if not endpoint:
        return jsonify({"error": "No endpoint configured"}), 400
    try:
        _broadcast_log(f"[splitter] AI splitting with {model}...")
        segments = split_content_ai(
            text, model_name=model, wpm=wpm,
            target_seconds=target_seconds,
            endpoint=endpoint, api_key=api_key,
        )
        summary = get_summary(segments)
        _broadcast_log(f"[splitter] ✓ AI split: {summary['count']} segments")
        return jsonify({"segments": segments, "summary": summary})
    except Exception as e:
        _broadcast_log(f"[splitter] ✗ AI split error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/translate/vi", methods=["POST"])
def translate_to_vi():
    body = request.get_json(force=True)
    text = str(body.get("text", "")).strip()
    source_type = str(body.get("source_type", "text")).strip() or "text"
    mode = str(body.get("mode", "fast")).strip().lower() or "fast"
    if not text:
        return jsonify({"error": "No text to translate"}), 400

    cfg = _load_config()
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    model_hint = str(body.get("model", "")).strip()
    model_name = _pick_translate_model(cfg, model_hint=model_hint, mode=mode)

    if not endpoint:
        return jsonify({"error": "No endpoint configured"}), 400
    if not model_name:
        return jsonify({"error": "No model configured for translation"}), 400

    if _is_likely_vietnamese(text):
        _broadcast_log(
            f"[api][translate][vi] bypass type={source_type} reason=already_vietnamese chars={len(text)}"
        )
        return jsonify({
            "translated_text": text,
            "source_type": source_type,
            "model": model_name,
            "cached": True,
            "bypass": "already_vietnamese",
        })

    cached = _translate_cache_get(model_name, text)
    if cached is not None:
        _broadcast_log(
            f"[api][translate][vi] cache_hit type={source_type} model={model_name} chars={len(text)}"
        )
        return jsonify({
            "translated_text": cached,
            "source_type": source_type,
            "model": model_name,
            "cached": True,
        })

    t0 = time.perf_counter()
    _broadcast_log(
        f"[api][translate][vi] request type={source_type} mode={mode} model={model_name} "
        f"chars={len(text)} endpoint={endpoint.rstrip('/')}"
    )
    try:
        translated, base = _translate_text_to_vi(
            text=text,
            model_name=model_name,
            endpoint=endpoint,
            api_key=api_key,
        )
        _translate_cache_set(model_name, text, translated)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _broadcast_log(
            f"[api][translate][vi] done type={source_type} model={model_name} "
            f"chars_in={len(text)} chars_out={len(translated)} elapsed_ms={elapsed_ms} endpoint={base}"
        )
        return jsonify({
            "translated_text": translated,
            "source_type": source_type,
            "model": model_name,
            "cached": False,
            "elapsed_ms": elapsed_ms,
        })
    except Exception as e:
        _broadcast_log(
            f"[api][translate][vi] error type={source_type} model={model_name} detail={e}"
        )
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES — Per-Step Pipeline & Prompt Regeneration
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/pipeline/step", methods=["POST"])
def pipeline_run_step():
    """Run a single pipeline step (write / split / video / continue_prompts)."""
    global _cancel_flag
    body = request.get_json(force=True)
    step = body.get("step", "")
    project_id = body.get("project_id", "")

    with _state_lock:
        if _pipeline_state["running"]:
            return jsonify({"error": "Pipeline already running"}), 409
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue is running"}), 409

    cfg = _load_config()
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    wpm = cfg.get("wpm", 130)
    target_seconds = cfg.get("target_seconds", 8.0)
    model_script = body.get("model", cfg.get("model", ""))
    model_video = body.get("model_video", cfg.get("model_video", model_script))

    # Load existing project data if project_id given
    proj_dir = None
    proj_data = {}
    if project_id:
        proj_dir = get_project_dir_by_id(project_id)
        if proj_dir:
            proj_data = load_project(os.path.join(proj_dir, "project.json"))

    # Current data from body or project
    script = body.get("script", proj_data.get("script", ""))
    segments = body.get("segments", proj_data.get("segments", []))
    video_prompts = body.get("video_prompts", proj_data.get("video_prompts", []))
    topic = body.get("topic", proj_data.get("topic", ""))
    style_name = body.get("style_name", proj_data.get("style_name", ""))
    video_style_name = body.get("video_style_name", proj_data.get("video_style_name", ""))

    # Resolve styles
    styles = _load_json(STYLES_PATH)
    style = next((s for s in styles if s.get("name") == style_name), styles[0] if styles else {"name": "General", "prompt": ""})
    video_styles = _load_json(VIDEO_STYLES_PATH)
    vstyle = next((s for s in video_styles if s.get("name") == video_style_name), None)
    video_style_prompt = vstyle.get("prompt", "") if vstyle else ""
    language = _normalize_language(body.get("language", proj_data.get("language", _DEFAULT_LANGUAGE)))
    duration = style.get("duration_minutes", 0)

    if step not in ("write", "split", "video", "continue_prompts"):
        return jsonify({"error": f"Invalid step: {step}"}), 400

    # Create proj_dir if not exists
    if not proj_dir:
        proj_dir, _, project_id = create_project_dir()

    save_project_incremental(
        proj_dir,
        topic=topic,
        style_name=style_name,
        video_style_name=video_style_name,
        model_name=model_script,
        model_video=model_video,
        language=language,
        project_id=project_id,
        status="in_progress",
    )

    _cancel_flag = False
    _pause_event.set()

    def run():
        with _state_lock:
            _pipeline_state.update({
                "running": True, "paused": False,
                "step": step, "progress": 0, "total": 1, "error": "",
                "script": script, "segments": segments, "video_prompts": video_prompts,
                "proj_dir": proj_dir, "project_id": project_id,
            })
        _broadcast_state(force=True)

        def _check_cancel_pause():
            if _cancel_flag:
                raise InterruptedError("Cancelled")
            _pause_event.wait()
            if _cancel_flag:
                raise InterruptedError("Cancelled")

        try:
            if step == "write":
                _broadcast_log(f"[step][write] start project_id={project_id} topic={topic}")

                def on_token(t):
                    with _state_lock:
                        _pipeline_state["script"] += t
                    _broadcast_script_chunk(t)

                with _state_lock:
                    _pipeline_state["script"] = ""
                _check_cancel_pause()
                _broadcast_log(
                    f"[api][write] request=POST /v1/chat/completions model={model_script} "
                    f"style={style_name or style.get('name', 'General')} language={language}"
                )
                new_script = write_content(
                    title=topic, style=style, model_name=model_script,
                    endpoint=endpoint, api_key=api_key,
                    wpm=wpm, target_sec=target_seconds,
                    duration_minutes=duration, language=language,
                    on_token=on_token, log_fn=_broadcast_log,
                    cancel_check=lambda: _cancel_flag,
                )
                _check_cancel_pause()
                with _state_lock:
                    _pipeline_state["script"] = new_script
                save_project_incremental(
                    proj_dir, topic=topic, script=new_script,
                    style_name=style_name, video_style_name=video_style_name,
                    model_name=model_script, model_video=model_video,
                    language=language, project_id=project_id,
                )
                _broadcast_log(f"[step][write] done project_id={project_id} chars={len(new_script)}")

            elif step == "split":
                if not script:
                    raise ValueError("No script to split")
                _broadcast_log(f"[step][split] start project_id={project_id} script_chars={len(script)}")
                _check_cancel_pause()
                new_segs, seg_meta = _split_segments_from_script(
                    script, wpm=wpm, target_seconds=target_seconds
                )
                _log_segment_validation(
                    new_segs, wpm=wpm, target_seconds=target_seconds,
                    meta=seg_meta, prefix="[segments][step]"
                )
                _check_cancel_pause()
                with _state_lock:
                    _pipeline_state["segments"] = new_segs
                save_project_incremental(
                    proj_dir, segments=new_segs, project_id=project_id,
                    topic=topic, style_name=style_name, video_style_name=video_style_name,
                    model_name=model_script, model_video=model_video, language=language,
                )
                _broadcast_log(
                    f"[step][split] done project_id={project_id} segments={len(new_segs)} "
                    f"mode={seg_meta.get('mode')} target_seconds={target_seconds}"
                )

            elif step == "video":
                if not segments:
                    raise ValueError("No segments for video prompts")
                _broadcast_log(f"[step][video] creating prompts project_id={project_id} segments={len(segments)}")
                with _state_lock:
                    _pipeline_state["video_prompts"] = []

                def on_ps(pl):
                    with _state_lock:
                        _pipeline_state["video_prompts"] = list(pl)
                    _broadcast_state()
                    save_project_incremental(
                        proj_dir, video_prompts=list(pl), project_id=project_id,
                        topic=topic, style_name=style_name, video_style_name=video_style_name,
                        model_name=model_script, model_video=model_video, language=language,
                    )

                def on_progress(_done, _total, _prompt):
                    _check_cancel_pause()

                _broadcast_log(
                    f"[api][video] start model={model_video} style={video_style_name or '(none)'} "
                    f"segments={len(segments)} endpoint={endpoint.rstrip('/')}"
                )
                new_prompts = generate_video_prompts(
                    segments, video_style=video_style_prompt,
                    model_name=model_video, endpoint=endpoint, api_key=api_key,
                    log_fn=_broadcast_log, cancel_check=lambda: _cancel_flag,
                    on_prompt_saved=on_ps, progress_fn=on_progress,
                )
                _check_cancel_pause()
                with _state_lock:
                    _pipeline_state["video_prompts"] = new_prompts
                _broadcast_log(f"[step][video] done project_id={project_id} prompts={len(new_prompts)}")

            elif step == "continue_prompts":
                if not segments:
                    raise ValueError("No segments")
                existing = list(video_prompts) if video_prompts else []
                missing = len(segments) - len(existing)
                if missing <= 0:
                    _broadcast_log(
                        f"[step][continue] skipped project_id={project_id} prompts_already_complete "
                        f"existing={len(existing)} segments={len(segments)}"
                    )
                else:
                    _broadcast_log(
                        f"[step][continue] continue_missing_prompts project_id={project_id} "
                        f"missing={missing} existing={len(existing)} segments={len(segments)}"
                    )

                    def on_ps2(pl):
                        with _state_lock:
                            _pipeline_state["video_prompts"] = list(pl)
                        _broadcast_state()
                        save_project_incremental(
                            proj_dir, video_prompts=list(pl), project_id=project_id,
                            topic=topic, style_name=style_name, video_style_name=video_style_name,
                            model_name=model_script, model_video=model_video, language=language,
                        )

                    def on_progress2(_done, _total, _prompt):
                        _check_cancel_pause()

                    _broadcast_log(
                        f"[api][video][continue] model={model_video} style={video_style_name or '(none)'} "
                        f"missing={missing} endpoint={endpoint.rstrip('/')}"
                    )
                    new_prompts = generate_video_prompts(
                        segments, video_style=video_style_prompt,
                        model_name=model_video, endpoint=endpoint, api_key=api_key,
                        log_fn=_broadcast_log, cancel_check=lambda: _cancel_flag,
                        on_prompt_saved=on_ps2, existing_prompts=existing,
                        progress_fn=on_progress2,
                    )
                    _check_cancel_pause()
                    with _state_lock:
                        _pipeline_state["video_prompts"] = new_prompts
                    _broadcast_log(
                        f"[step][continue] done project_id={project_id} prompts={len(new_prompts)} "
                        f"added={max(0, len(new_prompts) - len(existing))}"
                    )
            with _state_lock:
                _pipeline_state["step"] = "done"
                _pipeline_state["progress"] = 1
        except InterruptedError:
            with _state_lock:
                _pipeline_state["step"] = "stopped"
            save_project_incremental(proj_dir, project_id=project_id, status="stopped")
            _broadcast_log(f"[step][stop] stopped_by_user project_id={project_id}")
        except Exception as e:
            with _state_lock:
                _pipeline_state["step"] = "error"
                _pipeline_state["error"] = str(e)
            save_project_incremental(proj_dir, project_id=project_id, status="error")
            _broadcast_log(f"[step][error] project_id={project_id} detail={e}")
        finally:
            with _state_lock:
                _pipeline_state["running"] = False
                _pipeline_state["paused"] = False
            _broadcast_state(force=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({"status": "started", "step": step, "project_id": project_id})


@app.route("/api/pipeline/regenerate-prompt", methods=["POST"])
def regenerate_prompt():
    """Regenerate a single video prompt for a specific segment."""
    body = request.get_json(force=True)
    segment_index = body.get("index", -1)
    segment_id = body.get("segment_id", segment_index + 1)
    segment_text = body.get("text", "")
    video_style_name = body.get("video_style_name", "")
    project_id = body.get("project_id", "")

    cfg = _load_config()
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    model_video = body.get("model_video", cfg.get("model_video", cfg.get("model", "")))

    video_styles = _load_json(VIDEO_STYLES_PATH)
    vstyle = next((s for s in video_styles if s.get("name") == video_style_name), None)
    video_style_prompt = vstyle.get("prompt", "") if vstyle else ""

    if not segment_text:
        return jsonify({"error": "No segment text"}), 400

    try:
        try:
            segment_id = int(segment_id)
        except Exception:
            segment_id = segment_index + 1
        base = endpoint.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key and api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        _broadcast_log(
            f"[api][video][regen] request segment_id={segment_id} model={model_video} "
            f"style={video_style_name or '(none)'} endpoint={base}"
        )
        prompt = generate_video_prompt_single(
            segment_text, segment_id, video_style_prompt,
            model_video, endpoint, api_key,
            headers=headers, url=url,
            log_fn=_broadcast_log,
        )
        _broadcast_log(f"[api][video][regen] done segment_id={segment_id} status=ok")

        # Save to project if project_id given
        if project_id:
            proj_dir = get_project_dir_by_id(project_id)
            if proj_dir:
                proj_data = load_project(os.path.join(proj_dir, "project.json"))
                vp = proj_data.get("video_prompts", [])
                while len(vp) <= segment_index:
                    vp.append("")
                vp[segment_index] = prompt
                save_project_incremental(proj_dir, video_prompts=vp, project_id=project_id)

        return jsonify({"prompt": prompt, "index": segment_index})
    except Exception as e:
        _broadcast_log(f"[regen] ✗ Lỗi: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/output-dir", methods=["GET"])
def get_output_dir():
    from core.project_manager import OUTPUT_ROOT
    cfg = _normalize_config_paths()
    return jsonify({"output_dir": cfg.get("output_dir", OUTPUT_ROOT), "default": OUTPUT_ROOT})


@app.route("/api/output-dir", methods=["POST"])
def set_output_dir():
    body = request.get_json(force=True)
    path = body.get("path", "").strip()
    if not path:
        return jsonify({"error": "No path provided"}), 400
    try:
        os.makedirs(path, exist_ok=True)
        cfg = _save_config({"output_dir": path})
        output_dir = cfg.get("output_dir", path)
        _broadcast_log(f"[config] Output dir: {output_dir}")
        return jsonify({"output_dir": output_dir})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/output-dir/pick", methods=["POST"])
def pick_output_dir():
    body = request.get_json(silent=True) or {}
    cfg = _normalize_config_paths()
    initial_dir = (
        (body.get("initial_dir") or "").strip()
        or cfg.get("output_dir", "").strip()
        or os.path.join(BASE_DIR, "output")
    )

    path, pick_err = _pick_folder_native(initial_dir, "Chon thu muc luu project")
    if pick_err:
        return jsonify({"error": f"Folder picker unavailable: {pick_err}"}), 500

    if not path:
        return jsonify({"cancelled": True})

    try:
        os.makedirs(path, exist_ok=True)
        cfg = _save_config({"output_dir": path})
        output_dir = cfg.get("output_dir", path)
        _broadcast_log(f"[config] Output dir: {output_dir}")
        return jsonify({"output_dir": output_dir, "cancelled": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/p2p-download-dir", methods=["GET"])
def get_p2p_download_dir():
    cfg = _normalize_config_paths()
    default_dir = _default_p2p_download_dir(cfg)
    current_dir = str(cfg.get("p2p_download_dir", "")).strip() or default_dir
    return jsonify({"p2p_download_dir": current_dir, "default": default_dir})


@app.route("/api/p2p-download-dir", methods=["POST"])
def set_p2p_download_dir():
    body = request.get_json(force=True)
    path = body.get("path", "").strip()
    if not path:
        return jsonify({"error": "No path provided"}), 400
    try:
        os.makedirs(path, exist_ok=True)
        cfg = _save_config({"p2p_download_dir": path})
        p2p_dir = cfg.get("p2p_download_dir", path)
        _broadcast_log(f"[config] P2P download dir: {p2p_dir}")
        return jsonify({"p2p_download_dir": p2p_dir})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/p2p-download-dir/pick", methods=["POST"])
def pick_p2p_download_dir():
    body = request.get_json(silent=True) or {}
    cfg = _normalize_config_paths()
    initial_dir = (
        (body.get("initial_dir") or "").strip()
        or str(cfg.get("p2p_download_dir", "")).strip()
        or _default_p2p_download_dir(cfg)
    )

    path, pick_err = _pick_folder_native(initial_dir, "Chon thu muc luu file P2P")
    if pick_err:
        return jsonify({"error": f"Folder picker unavailable: {pick_err}"}), 500

    if not path:
        return jsonify({"cancelled": True})

    try:
        os.makedirs(path, exist_ok=True)
        cfg = _save_config({"p2p_download_dir": path})
        p2p_dir = cfg.get("p2p_download_dir", path)
        _broadcast_log(f"[config] P2P download dir: {p2p_dir}")
        return jsonify({"p2p_download_dir": p2p_dir, "cancelled": False})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===========================================================================
# ROUTES - P2P File Share
# ===========================================================================

@app.route("/api/p2p/shares", methods=["GET"])
def p2p_list_shares():
    with _p2p_lock:
        shares = sorted(
            (_p2p_share_summary(s, include_files=True, include_paths=True) for s in _p2p_shares),
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )
    return jsonify({"shares": shares})


@app.route("/api/p2p/pick-files", methods=["POST"])
def p2p_pick_files():
    body = request.get_json(silent=True) or {}
    initial_dir = (body.get("initial_dir") or "").strip() or os.path.expanduser("~")

    selected, pick_err = _pick_files_native(initial_dir, "Choose files for P2P share")
    if pick_err:
        return jsonify({"error": f"File picker unavailable: {pick_err}"}), 500

    if not selected:
        return jsonify({"cancelled": True})

    files = _normalize_p2p_files([{"path": p, "rel_path": os.path.basename(p)} for p in selected])
    return jsonify({
        "cancelled": False,
        "files": files,
        "suggested_name": _default_p2p_name(),
        "file_count": len(files),
    })


@app.route("/api/p2p/pick-folder", methods=["POST"])
def p2p_pick_folder():
    body = request.get_json(silent=True) or {}
    initial_dir = (body.get("initial_dir") or "").strip() or os.path.expanduser("~")

    folder, pick_err = _pick_folder_native(initial_dir, "Choose folder for P2P share")
    if pick_err:
        return jsonify({"error": f"Folder picker unavailable: {pick_err}"}), 500

    if not folder:
        return jsonify({"cancelled": True})

    files = _collect_folder_files(folder)
    suggested_name = os.path.basename(folder.rstrip("\\/")) or _default_p2p_name()
    return jsonify({
        "cancelled": False,
        "folder_path": folder,
        "files": files,
        "suggested_name": suggested_name,
        "file_count": len(files),
    })


@app.route("/api/p2p/upload-files", methods=["POST"])
def p2p_upload_files():
    return jsonify({
        "error": "Browser upload has been disabled. Use /api/p2p/pick-files or /api/p2p/pick-folder to keep original files unchanged."
    }), 410


@app.route("/api/p2p/shares", methods=["POST"])
def p2p_create_share():
    body = request.get_json(force=True) or {}
    name = str(body.get("name", "")).strip() or _default_p2p_name()
    files = _normalize_p2p_files(body.get("files", []))
    if not files:
        return jsonify({"error": "No files selected"}), 400

    with _p2p_lock:
        token = _new_p2p_token({s.get("token", "") for s in _p2p_shares})
        now_iso = datetime.now().isoformat(timespec="seconds")
        share = {
            "token": token,
            "name": name,
            "files": files,
            "created_at": now_iso,
            "updated_at": now_iso,
            "download_count": 0,
            "last_download_at": "",
            "last_download_dir": "",
        }
        _p2p_shares.append(share)
        _save_p2p_shares_locked()
    _broadcast_log(f"[p2p] create token={token} files={len(files)} name={name}")
    return jsonify({"share": _p2p_share_summary(share, include_files=True, include_paths=True)})


@app.route("/api/p2p/shares/<token>", methods=["GET"])
def p2p_get_share(token):
    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        return jsonify({"share": _p2p_share_summary(share, include_files=True, include_paths=True)})


@app.route("/api/p2p/shares/<token>", methods=["PUT"])
def p2p_update_share(token):
    body = request.get_json(force=True) or {}
    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        if "name" in body:
            next_name = str(body.get("name", "")).strip() or _default_p2p_name()
            share["name"] = next_name
        if "files" in body:
            next_files = _normalize_p2p_files(body.get("files", []))
            if not next_files:
                return jsonify({"error": "No files selected"}), 400
            share["files"] = next_files
        share["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_p2p_shares_locked()
        summary = _p2p_share_summary(share, include_files=True, include_paths=True)
    _broadcast_log(f"[p2p] update token={summary.get('token')} files={summary.get('file_count')}")
    return jsonify({"share": summary})


@app.route("/api/p2p/shares/<token>/files/add", methods=["POST"])
def p2p_add_files(token):
    body = request.get_json(force=True) or {}
    add_files = _normalize_p2p_files(body.get("files", []))
    if not add_files:
        return jsonify({"error": "No files selected"}), 400

    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        merged = _dedupe_p2p_files(list(share.get("files", [])) + add_files)
        share["files"] = merged
        share["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_p2p_shares_locked()
        summary = _p2p_share_summary(share, include_files=True, include_paths=True)
    _broadcast_log(f"[p2p] add-files token={summary.get('token')} now={summary.get('file_count')}")
    return jsonify({"share": summary})


@app.route("/api/p2p/shares/<token>/files/remove", methods=["POST"])
def p2p_remove_files(token):
    body = request.get_json(force=True) or {}
    raw_paths = body.get("paths", [])
    if not isinstance(raw_paths, list) or not raw_paths:
        return jsonify({"error": "No file paths selected"}), 400
    remove_paths = {os.path.abspath(str(p)).lower() for p in raw_paths if str(p).strip()}

    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        kept = [f for f in share.get("files", []) if os.path.abspath(str(f.get("path", ""))).lower() not in remove_paths]
        share["files"] = kept
        share["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_p2p_shares_locked()
        summary = _p2p_share_summary(share, include_files=True, include_paths=True)
    _broadcast_log(f"[p2p] remove-files token={summary.get('token')} now={summary.get('file_count')}")
    return jsonify({"share": summary})


@app.route("/api/p2p/shares/<token>", methods=["DELETE"])
def p2p_delete_share(token):
    t = _sanitize_p2p_token(token)
    if len(t) != 6:
        return jsonify({"error": "Invalid token"}), 400
    with _p2p_lock:
        idx = next((i for i, s in enumerate(_p2p_shares) if s.get("token") == t), -1)
        if idx < 0:
            return jsonify({"error": "Token not found"}), 404
        removed = _p2p_shares.pop(idx)
        _save_p2p_shares_locked()
    _broadcast_log(f"[p2p] delete token={removed.get('token')} name={removed.get('name')}")
    return jsonify({"ok": True, "token": removed.get("token", "")})


@app.route("/api/p2p/shares/<token>/info", methods=["GET"])
def p2p_share_info(token):
    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        return jsonify({"share": _p2p_share_summary(share, include_files=False, include_paths=False)})



# ── WebRTC P2P helper endpoints ──────────────────────────────────────────────

@app.route("/api/p2p/share-meta/<token>", methods=["GET"])
def p2p_share_meta(token):
    """Return file list + metadata for a share (used by WebRTC receiver to know what to request)."""
    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        files_out = []
        for item in share.get("files", []):
            p = item.get("path", "")
            if os.path.isfile(p):
                files_out.append({
                    "name": item.get("name", os.path.basename(p)),
                    "rel_path": item.get("rel_path", item.get("name", os.path.basename(p))),
                    "size": os.path.getsize(p),
                })
        return jsonify({
            "token": share.get("token", ""),
            "name": share.get("name", ""),
            "files": files_out,
            "total_size": sum(f["size"] for f in files_out),
        })


@app.route("/api/p2p/stream-file", methods=["GET"])
def p2p_stream_file():
    """Stream a file from sender's disk to browser (for WebRTC sender to read and send via DataChannel)."""
    token = request.args.get("token", "").strip().upper()
    rel_path = request.args.get("rel_path", "").strip()
    if not token or not rel_path:
        return jsonify({"error": "Missing token or rel_path"}), 400

    with _p2p_lock:
        share = _find_p2p_share_locked(token)
        if not share:
            return jsonify({"error": "Token not found"}), 404
        target_file = None
        for item in share.get("files", []):
            item_rel = item.get("rel_path", item.get("name", ""))
            if item_rel == rel_path and os.path.isfile(item.get("path", "")):
                target_file = item["path"]
                break
        if not target_file:
            return jsonify({"error": "File not found"}), 404

    file_size = os.path.getsize(target_file)
    CHUNK = 64 * 1024  # 64KB

    def generate():
        with open(target_file, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                yield chunk

    return app.response_class(
        generate(),
        mimetype="application/octet-stream",
        headers={
            "Content-Length": str(file_size),
            "Content-Disposition": f'attachment; filename="{os.path.basename(target_file)}"',
            "X-File-Size": str(file_size),
        },
    )


_webrtc_uploads: dict = {}  # session_id -> {dir, files}

@app.route("/api/p2p/save-chunk", methods=["POST"])
def p2p_save_chunk():
    """Save a chunk received via WebRTC to local disk (called by receiver's browser)."""
    session_id = request.args.get("session", "")
    rel_path = request.args.get("rel_path", "")
    offset = int(request.args.get("offset", "0"))
    if not session_id or not rel_path:
        return jsonify({"error": "Missing session or rel_path"}), 400

    cfg = _load_config()
    download_root = os.path.abspath(_get_p2p_download_dir(cfg))

    if session_id not in _webrtc_uploads:
        session_dir = _unique_dir_path(download_root, f"webrtc_{session_id[:8]}")
        os.makedirs(session_dir, exist_ok=True)
        _webrtc_uploads[session_id] = {"dir": session_dir, "files": set()}

    info = _webrtc_uploads[session_id]
    safe_rel = rel_path.replace("\\", "/").lstrip("/")
    target = os.path.abspath(os.path.join(info["dir"], safe_rel.replace("/", os.sep)))
    if not _path_is_within_dir(target, info["dir"]):
        return jsonify({"error": "Invalid path"}), 400

    os.makedirs(os.path.dirname(target), exist_ok=True)
    chunk_data = request.get_data()
    mode = "r+b" if os.path.isfile(target) else "wb"
    with open(target, mode) as f:
        f.seek(offset)
        f.write(chunk_data)
    info["files"].add(safe_rel)

    return jsonify({"ok": True, "written": len(chunk_data)})


@app.route("/api/p2p/save-done", methods=["POST"])
def p2p_save_done():
    """Finalize WebRTC download session — rename temp dir to final name."""
    data = request.get_json(force=True) or {}
    session_id = data.get("session", "")
    share_name = data.get("name", "webrtc_download")
    if session_id not in _webrtc_uploads:
        return jsonify({"error": "Session not found"}), 404

    info = _webrtc_uploads.pop(session_id)
    cfg = _load_config()
    download_root = os.path.abspath(_get_p2p_download_dir(cfg))
    safe_name = _safe_dir_name(share_name, "download")
    final_dir = os.path.join(download_root, safe_name)
    if os.path.exists(final_dir):
        final_dir = _unique_dir_path(download_root, safe_name)

    try:
        shutil.move(info["dir"], final_dir)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    file_count = len(info["files"])
    # Calculate total size
    total_size = 0
    for fn in info["files"]:
        fp = os.path.join(final_dir, fn)
        if os.path.isfile(fp):
            total_size += os.path.getsize(fp)

    # Create download entry in p2p_shares for download list
    now_iso = datetime.now().isoformat(timespec="seconds")
    dl_entry = {
        "token": f"dl-{session_id[:8]}",
        "name": share_name,
        "files": [{"rel_path": fn} for fn in info["files"]],
        "created_at": now_iso,
        "updated_at": now_iso,
        "download_count": 1,
        "last_download_at": now_iso,
        "last_download_dir": final_dir,
        "file_count": file_count,
        "total_size": total_size,
        "type": "download",
    }
    with _p2p_lock:
        _p2p_shares.append(dl_entry)
        _save_p2p_shares_locked()

    _broadcast_log(f"[p2p][webrtc] download done: {file_count} files -> {final_dir}")
    return jsonify({"ok": True, "saved_dir": final_dir, "file_count": file_count})

@app.route("/api/queue", methods=["GET"])
def get_queue():
    with _queue_lock:
        return jsonify({
            "queue": list(_shared_queue),
            "running": _queue_running,
            "current": _queue_progress["current"],
            "total": _queue_progress["total"],
            "current_topic": _queue_progress["current_topic"],
        })


@app.route("/api/queue", methods=["POST"])
def add_queue_item():
    item = _sanitize_queue_item(request.get_json(force=True) or {})
    if not isinstance(item, dict):
        return jsonify({"error": "Invalid queue item"}), 400
    if not item.get("topic"):
        return jsonify({"error": "Topic is required"}), 400
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue is running"}), 409
        _shared_queue.append(item)
        _save_queue_state_locked()
        queue_snapshot = list(_shared_queue)
    _broadcast_log(f"[queue] Added: {item.get('topic', '')[:40]}")
    return jsonify({"queue": queue_snapshot})


@app.route("/api/queue/<int:idx>", methods=["PUT"])
def edit_queue_item(idx):
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue is running"}), 409
        if not (0 <= idx < len(_shared_queue)):
            return jsonify({"error": "Invalid index"}), 400
        item = _sanitize_queue_item(request.get_json(force=True) or {})
        if not isinstance(item, dict):
            return jsonify({"error": "Invalid queue item"}), 400
        if not item.get("topic"):
            return jsonify({"error": "Topic is required"}), 400
        _shared_queue[idx] = item
        _save_queue_state_locked()
        queue_snapshot = list(_shared_queue)
        _broadcast_log(f"[queue] Updated #{idx+1}")
    return jsonify({"queue": queue_snapshot})


@app.route("/api/queue/<int:idx>", methods=["DELETE"])
def delete_queue_item(idx):
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue is running"}), 409
        if not (0 <= idx < len(_shared_queue)):
            return jsonify({"error": "Invalid index"}), 400
        removed = _shared_queue.pop(idx)
        _save_queue_state_locked()
        queue_snapshot = list(_shared_queue)
        _broadcast_log(f"[queue] Removed: {removed.get('topic', '')[:40]}")
    return jsonify({"queue": queue_snapshot})


@app.route("/api/queue/clear", methods=["POST"])
def clear_queue():
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue is running"}), 409
        _shared_queue.clear()
        _save_queue_state_locked()
        _queue_progress["current"] = 0
        _queue_progress["total"] = 0
        _queue_progress["current_topic"] = ""
    _broadcast_log("[queue] Cleared all")
    return jsonify({"queue": []})


@app.route("/api/queue/start", methods=["POST"])
def start_queue():
    global _queue_running, _cancel_flag
    with _state_lock:
        if _pipeline_state["running"]:
            return jsonify({"error": "Pipeline is running"}), 409
    with _queue_lock:
        if _queue_running:
            return jsonify({"error": "Queue already running"}), 409
        if not _shared_queue:
            return jsonify({"error": "Queue is empty"}), 400
        _cancel_flag = False
        _queue_running = True
        _queue_progress["current"] = 0
        _queue_progress["total"] = len(_shared_queue)
        _queue_progress["current_topic"] = ""
        total = len(_shared_queue)
    t = threading.Thread(target=_run_queue, daemon=True)
    t.start()
    return jsonify({"status": "started", "count": total})


def _run_queue():
    global _queue_running, _cancel_flag
    with _queue_lock:
        total = _queue_progress["total"] or len(_shared_queue)
    _broadcast_log(f"[queue] Starting queue with {total} items")

    idx = 0
    cancelled = False
    while True:
        with _queue_lock:
            if not _shared_queue:
                break
            item = _shared_queue.pop(0)
            _save_queue_state_locked()
            idx += 1
            _queue_progress["current"] = idx
            _queue_progress["current_topic"] = item.get("topic", "")
            queue_snapshot = list(_shared_queue)
            current = _queue_progress["current"]
            total_now = _queue_progress["total"] or total

        _broadcast_log(f"[queue] Item {idx}/{total}: {item.get('topic', '')[:40]}")
        _broadcast({
            "type": "queue_state",
            "queue": queue_snapshot,
            "running": True,
            "current": current,
            "total": total_now,
            "current_topic": item.get("topic", ""),
        })
        _run_pipeline(item)
        if _cancel_flag:
            cancelled = True
            _broadcast_log("[queue] Stop requested - keep remaining items in queue")
            break

        with _queue_lock:
            still_has_items = bool(_shared_queue)
        if still_has_items:
            time.sleep(1)

    with _queue_lock:
        _queue_running = False
        _queue_progress["current"] = 0
        _queue_progress["total"] = 0
        _queue_progress["current_topic"] = ""
        queue_snapshot = list(_shared_queue)

    if cancelled:
        _broadcast_log(f"[queue] Queue stopped ({idx}/{total} items processed)")
    else:
        _broadcast_log(f"[queue] Queue completed ({idx} items)")
    _cancel_flag = False
    _broadcast({
        "type": "queue_state",
        "queue": queue_snapshot,
        "running": False,
        "current": 0,
        "total": 0,
        "current_topic": "",
    })

# ── Auto-update check via GitHub API (no Git required) ───────────────────────
_GITHUB_REPO = "binhunicorps/AutoStudio"
_VERSION_FILE = os.path.join(BASE_DIR, "VERSION")
_update_cache: dict = {"checked": False, "has_update": False, "local": "", "remote": "", "download_url": "", "error": ""}


def _read_local_version() -> str:
    try:
        with open(_VERSION_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _check_for_updates():
    """Background check: compare local VERSION with latest GitHub release."""
    local_ver = _read_local_version()
    cfg = _load_config()
    github_token = cfg.get("github_token", "").strip()
    try:
        import requests as _req
        headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        r = _req.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
            timeout=10, headers=headers,
        )
        if r.status_code == 404:
            # No releases yet, try tags
            r2 = _req.get(
                f"https://api.github.com/repos/{_GITHUB_REPO}/tags",
                timeout=10, headers=headers,
            )
            if r2.status_code == 200 and r2.json():
                latest_tag = r2.json()[0].get("name", "")
                remote_ver = latest_tag.lstrip("v")
                download_url = f"https://github.com/{_GITHUB_REPO}/archive/refs/tags/{latest_tag}.zip"
            else:
                _update_cache.update(checked=True, error="no releases")
                return
        elif r.status_code == 200:
            data = r.json()
            remote_ver = data.get("tag_name", "").lstrip("v")
            download_url = data.get("zipball_url", f"https://github.com/{_GITHUB_REPO}/archive/refs/heads/main.zip")
        else:
            _update_cache.update(checked=True, error=f"GitHub API {r.status_code}")
            return

        has_update = remote_ver != local_ver and remote_ver > local_ver
        _update_cache.update(
            checked=True, has_update=has_update,
            local=local_ver, remote=remote_ver,
            download_url=download_url,
            error="",
        )
        if has_update:
            _broadcast_log(f"[update] Co ban cap nhat moi (v{local_ver} -> v{remote_ver})")
    except Exception as e:
        _update_cache.update(checked=True, error=str(e)[:100])


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    data = request.get_json(silent=True) or {}
    folder = data.get("path", "").strip()
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "Thư mục không tồn tại"}), 400
    subprocess.Popen(["explorer", os.path.normpath(folder)])
    return jsonify({"ok": True})


@app.route("/api/check-update", methods=["GET"])
def api_check_update():
    return jsonify(_update_cache)


@app.route("/api/apply-update", methods=["POST"])
def api_apply_update():
    """Download latest release, extract to new versioned folder, copy config, launch new version."""
    if not _update_cache.get("has_update"):
        return jsonify({"error": "Không có bản cập nhật"}), 400
    download_url = _update_cache.get("download_url", "")
    remote_ver = _update_cache.get("remote", "")
    if not download_url:
        return jsonify({"error": "Không có URL tải về"}), 400

    parent_dir = os.path.dirname(BASE_DIR)  # e.g., D:\Software
    new_folder_name = f"AutoStudio-{remote_ver}"
    new_install_dir = os.path.join(parent_dir, new_folder_name)
    staging_dir = os.path.join(parent_dir, "_update_staging")
    zip_path = os.path.join(parent_dir, "_update.zip")

    try:
        # Clean previous staging
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        if os.path.isfile(zip_path):
            os.remove(zip_path)
        if os.path.isdir(new_install_dir):
            shutil.rmtree(new_install_dir, ignore_errors=True)

        # Download ZIP
        import requests as _req
        cfg = _load_config()
        github_token = cfg.get("github_token", "").strip()
        headers = {"Accept": "application/vnd.github.v3+json"}
        if github_token:
            headers["Authorization"] = f"token {github_token}"
        _broadcast_log("[update] Dang tai ban cap nhat...")
        r = _req.get(download_url, headers=headers, stream=True, timeout=120,
                     allow_redirects=True)
        if r.status_code != 200:
            return jsonify({"error": f"HTTP {r.status_code} khi tai ZIP"}), 500
        with open(zip_path, "wb") as zf:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                zf.write(chunk)
        _broadcast_log(f"[update] Da tai xong ({os.path.getsize(zip_path)} bytes)")

        # Extract ZIP
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(staging_dir)
        os.remove(zip_path)

        # Find the extracted top-level folder (GitHub ZIPs have one)
        extracted_items = os.listdir(staging_dir)
        source_dir = staging_dir
        if len(extracted_items) == 1 and os.path.isdir(os.path.join(staging_dir, extracted_items[0])):
            source_dir = os.path.join(staging_dir, extracted_items[0])

        # Rename extracted folder to new versioned name
        shutil.move(source_dir, new_install_dir)
        # Clean staging if it's still there
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)

        # Copy user config from current version to new version
        user_data_files = ["config.json", "styles.json", "video_styles.json", "p2p_shares.json"]
        new_data_dir = os.path.join(new_install_dir, "data")
        os.makedirs(new_data_dir, exist_ok=True)
        for fname in user_data_files:
            src = os.path.join(BASE_DIR, "data", fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(new_data_dir, fname))

        # Copy runtime folder (Python environment) — use robocopy in batch for speed
        # Copy output folder reference from config
        _broadcast_log("[update] Da sao chep cau hinh nguoi dung")

        # Generate updater batch script in parent dir
        bat_path = os.path.join(parent_dir, "_do_update.bat")
        runtime_src = os.path.join(BASE_DIR, "runtime")
        runtime_dst = os.path.join(new_install_dir, "runtime")
        bat_content = f"""@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   Auto Studio - Dang cap nhat v{remote_ver}
echo ========================================
echo.
echo Tat server cu...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
timeout /t 2 /nobreak >nul

echo Sao chep runtime...
if exist "{runtime_src}" (
    robocopy "{runtime_src}" "{runtime_dst}" /E /NFL /NDL /NP /NJH /NJS
)

echo Khoi dong phien ban moi...
cd /d "{new_install_dir}"
if exist "AutoStudio.vbs" (
    start "" wscript "AutoStudio.vbs"
) else (
    start "" cmd /c "scripts\\run_server.bat"
)

echo Cap nhat hoan tat!
echo Phien ban moi: {new_install_dir}
timeout /t 3 /nobreak >nul
del "%~f0"
"""
        with open(bat_path, "w", encoding="ascii", errors="replace") as f:
            f.write(bat_content)
        _broadcast_log(f"[update] San sang. Phien ban moi: {new_install_dir}")

        # Schedule batch execution and server shutdown
        def _run_updater():
            time.sleep(1)
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                cwd=parent_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            time.sleep(0.5)
            os._exit(0)

        threading.Thread(target=_run_updater, daemon=True).start()
        return jsonify({"ok": True, "new_dir": new_install_dir, "message": f"Dang cai dat v{remote_ver}..."})

    except Exception as e:
        _broadcast_log(f"[update] Loi: {e}")
        if os.path.isfile(zip_path):
            try: os.remove(zip_path)
            except: pass
        if os.path.isdir(staging_dir):
            try: shutil.rmtree(staging_dir, ignore_errors=True)
            except: pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/version", methods=["GET"])
def api_version():
    return jsonify({"version": _read_local_version()})


def _startup_model_check():
    time.sleep(2)
    cfg = _load_config()
    endpoint = cfg.get("endpoint", "")
    api_key = cfg.get("api_key", "")
    if not endpoint:
        _broadcast_log("[app] No endpoint configured - go to Settings")
        return
    try:
        models = fetch_models(endpoint, api_key)
        _broadcast_log(f"[app] Connected - {len(models)} models found")
        ready, probe_meta = _probe_models_ready(endpoint, api_key, models, log_prefix="[app][models]")
        if probe_meta.get("fallback_used"):
            _broadcast_log("[app][models] readiness check unavailable; fallback to listed models")
        _save_config({"available_models": models, "ready_models": ready})
        _broadcast_log(f"[app] {len(ready)}/{len(models)} models ready")
    except Exception as e:
        _broadcast_log(f"[app] Could not connect to API: {e}")


if __name__ == "__main__":
    threading.Thread(target=_startup_model_check, daemon=True).start()
    threading.Thread(target=_check_for_updates, daemon=True).start()
    print("\n  +--------------------------------------+")
    print("  |  Auto Studio — AI Video Studio    |")
    print("  |  http://localhost:5000               |")
    print("  +--------------------------------------+\n")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

