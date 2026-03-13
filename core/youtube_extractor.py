"""
core/youtube_extractor.py
Extract subtitle, title, description from YouTube via yt-dlp Python API.
"""

import re
import json
import os
import tempfile


def _ensure_yt_dlp():
    """Import yt-dlp, raise clear error if not installed."""
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        raise ImportError(
            "yt-dlp chưa được cài đặt. Chạy: pip install yt-dlp"
        )


def _clean_subtitle_text(raw: str) -> str:
    """Remove VTT/SRT timestamps, tags, dedup lines."""
    lines = raw.splitlines()
    seen = set()
    clean = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip numeric cue indices
        if re.match(r"^\d+$", line):
            continue
        # Skip VTT header
        if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        # Skip timestamp lines  00:00:01.000 --> 00:00:04.000
        if re.match(r"^\d{2}:\d{2}[:\.]", line):
            continue
        # Remove HTML/VTT tags
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\{[^}]+\}", "", line)
        line = line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        clean.append(line)
    return "\n".join(clean)


def _parse_json3_subtitle(raw: str) -> str:
    """Parse YouTube json3 subtitle format into clean text."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""

    events = data.get("events", [])
    sentences = []
    current_words = []

    for event in events:
        segs = event.get("segs")
        if not segs:
            # Event without segs = line break
            if current_words:
                line = "".join(current_words).strip()
                if line:
                    sentences.append(line)
                current_words = []
            continue
        for seg in segs:
            text = seg.get("utf8", "")
            if text == "\n":
                if current_words:
                    line = "".join(current_words).strip()
                    if line:
                        sentences.append(line)
                    current_words = []
            else:
                current_words.append(text)

    # Flush remaining
    if current_words:
        line = "".join(current_words).strip()
        if line:
            sentences.append(line)

    # Dedup consecutive identical lines
    deduped = []
    for s in sentences:
        if not deduped or s != deduped[-1]:
            deduped.append(s)
    return "\n".join(deduped)


def _parse_subtitle_raw(raw: str, fmt: str = "") -> str:
    """Auto-detect and parse subtitle content."""
    stripped = raw.strip()
    # Detect json3
    if stripped.startswith("{") and "events" in stripped[:200]:
        return _parse_json3_subtitle(raw)
    # Detect json3 array variant
    if stripped.startswith("[") and "utf8" in stripped[:500]:
        try:
            return _parse_json3_subtitle('{"events":' + raw + '}')
        except Exception:
            pass
    # VTT/SRT
    return _clean_subtitle_text(raw)


def extract_youtube_info(
    url: str,
    preferred_langs: list[str] | None = None,
    log_fn=None,
) -> dict:
    yt_dlp = _ensure_yt_dlp()
    _log = log_fn or (lambda m: None)

    if not preferred_langs:
        preferred_langs = ["vi", "en", "ja", "ko", "zh", "es", "fr", "de", "pt", "ru"]

    _log(f"[youtube] Đang tải thông tin video: {url}")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": preferred_langs,
        "subtitlesformat": "vtt/best",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return {"ok": False, "error": f"Không thể tải thông tin video: {str(e)[:200]}"}

    title = info.get("title", "")
    description = info.get("description", "")
    duration = info.get("duration", 0) or 0
    channel = info.get("channel", "") or info.get("uploader", "")
    view_count = info.get("view_count", 0) or 0
    upload_date = info.get("upload_date", "")

    _log(f"[youtube] Video: {title} | {duration}s | {channel}")

    subtitles_text = ""
    subtitle_lang = ""

    sub_sources = [
        ("manual", info.get("subtitles") or {}),
        ("auto", info.get("automatic_captions") or {}),
    ]

    # Format priority: vtt first, then srv1/srv2/srv3, then json3
    fmt_priority = ["vtt", "srv1", "srv2", "srv3", "json3"]

    for source_type, subs_dict in sub_sources:
        if subtitles_text:
            break
        for lang in preferred_langs:
            if lang not in subs_dict:
                continue
            _log(f"[youtube] Tìm thấy subtitle {source_type} [{lang}]")
            sub_entries = subs_dict[lang]

            # Pick best format
            sub_url = None
            sub_fmt = ""
            for fmt in fmt_priority:
                for entry in sub_entries:
                    if entry.get("ext", "") == fmt:
                        sub_url = entry.get("url", "")
                        sub_fmt = fmt
                        break
                if sub_url:
                    break
            if not sub_url and sub_entries:
                sub_url = sub_entries[0].get("url", "")
                sub_fmt = sub_entries[0].get("ext", "")

            if sub_url:
                try:
                    import requests
                    _log(f"[youtube] Tải subtitle [{lang}] format={sub_fmt}...")
                    r = requests.get(sub_url, timeout=30)
                    r.raise_for_status()
                    subtitles_text = _parse_subtitle_raw(r.text, sub_fmt)
                    subtitle_lang = lang
                    _log(f"[youtube] Đã tải subtitle [{lang}]: {len(subtitles_text)} ký tự")
                except Exception as e:
                    _log(f"[youtube] Lỗi tải subtitle [{lang}]: {e}")
            break

    if not subtitles_text:
        _log("[youtube] Không tìm thấy subtitle. Sẽ dùng mô tả video làm nguồn.")

    tags = info.get("tags") or []

    result = {
        "ok": True,
        "title": title,
        "description": description,
        "subtitles_text": subtitles_text,
        "subtitle_lang": subtitle_lang,
        "duration": duration,
        "channel": channel,
        "view_count": view_count,
        "upload_date": upload_date,
        "tags": tags,
        "url": url,
    }
    return result
