"""
core/video_prompter.py
Generate one video prompt per segment with retry and safe logging.
"""

import time
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry


MAX_RETRIES = 4
RETRY_DELAY = 2
READ_TIMEOUTS = (60, 90, 120, 180)
ERROR_DETAIL_LIMIT = 280
MAX_VIDEO_STYLE_CHARS = 1400

SYSTEM_PROMPT = """You are an expert at creating prompts for AI video generators.

Task: Given a content segment, write ONE short English prompt describing the visual/video that matches this content.

{style_instruction}

Rules:
1. Describe a specific visual scene: subject, action, setting, lighting, mood.
2. English only, concise (1-2 sentences, max 50 words).
3. Do not repeat the content text; describe what viewers should see.
4. Return only prompt text. No quotes, no explanation."""


def _get_session() -> requests.Session:
    """Return a reusable session with connection pooling."""
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=10, max_retries=_Retry(total=0))
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def _compact_text(value: str, limit: int = ERROR_DETAIL_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _error_detail_from_response(resp) -> str:
    if resp is None:
        return ""

    # Prefer structured API error if available.
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or err.get("detail") or err.get("code") or str(err)
            elif err:
                detail = str(err)
            else:
                detail = data.get("message") or data.get("detail") or str(data)
        else:
            detail = str(data)
    except Exception:
        detail = ""

    if not detail:
        try:
            detail = resp.text or ""
        except Exception:
            detail = ""
    return _compact_text(detail)


def _is_retryable_status(status_code: int) -> bool:
    # User requirement: any API error code should be logged then retried.
    return status_code >= 400


def _prepare_video_style(style_text: str) -> str:
    """Compact overly long style prompts to reduce latency/timeouts."""
    raw = str(style_text or "").replace("\r", "").strip()
    if not raw:
        return ""

    # Large example blocks make every per-segment call slower.
    lower = raw.lower()
    cut_markers = (
        "### example",
        "\nexample\n",
        "\ninput:\n",
    )
    cut_pos = len(raw)
    for marker in cut_markers:
        idx = lower.find(marker)
        if idx >= 0:
            cut_pos = min(cut_pos, idx)
    if cut_pos < len(raw):
        raw = raw[:cut_pos].strip()

    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw).strip()

    if len(raw) > MAX_VIDEO_STYLE_CHARS:
        raw = raw[: MAX_VIDEO_STYLE_CHARS - 3].rstrip() + "..."
    return raw


def _make_request(url, payload, headers, session=None, retries=MAX_RETRIES, log_fn=None, log_prefix=""):
    """POST with progressive timeout: starts short, grows on retry."""
    _log = log_fn or (lambda m: None)
    http = session or requests
    last_err = None

    for attempt in range(1, retries + 1):
        read_timeout = READ_TIMEOUTS[min(attempt - 1, len(READ_TIMEOUTS) - 1)]
        start = time.time()
        resp = None
        try:
            if attempt > 1:
                _log(f"{log_prefix}retry attempt={attempt}/{retries} timeout={read_timeout}s")

            resp = http.post(url, json=payload, headers=headers, timeout=(10, read_timeout))
            elapsed = round(time.time() - start, 1)
            detail = _error_detail_from_response(resp)

            if resp.status_code == 200:
                _log(f"{log_prefix}status=200 elapsed={elapsed}s")
                return resp

            if detail:
                _log(f"{log_prefix}status={resp.status_code} elapsed={elapsed}s error={detail}")
            else:
                _log(f"{log_prefix}status={resp.status_code} elapsed={elapsed}s")

            last_err = f"HTTP {resp.status_code}{f': {detail}' if detail else ''}"
            if _is_retryable_status(resp.status_code) and attempt < retries:
                _log(f"{log_prefix}retry_wait={RETRY_DELAY}s")
                time.sleep(RETRY_DELAY)
                continue

            resp.raise_for_status()
            raise RuntimeError(last_err)

        except (requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as e:
            elapsed = round(time.time() - start, 1)
            last_err = f"Timeout: {e}"
            _log(
                f"{log_prefix}timeout elapsed={elapsed}s limit={read_timeout}s "
                f"error={_compact_text(f'{e.__class__.__name__}: {e}')}"
            )
            if attempt < retries:
                time.sleep(1)

        except requests.exceptions.ConnectionError as e:
            last_err = f"Connection: {e}"
            wait = RETRY_DELAY * attempt
            _log(f"{log_prefix}connection_error wait={wait}s error={_compact_text(e)}")
            if attempt < retries:
                time.sleep(wait)

        except requests.exceptions.HTTPError as e:
            status = resp.status_code if resp is not None else "unknown"
            detail = _error_detail_from_response(resp)
            if detail:
                _log(f"{log_prefix}http_error status={status} error={detail}")
            else:
                _log(f"{log_prefix}http_error status={status}")
            raise RuntimeError(f"HTTP {status}{f': {detail}' if detail else ''}") from e

    raise RuntimeError(f"Failed after {retries} retries: {last_err}")


def generate_video_prompt_single(
    segment_text: str,
    segment_id: int,
    video_style: str,
    model_name: str,
    endpoint: str,
    api_key: str,
    headers: dict,
    url: str,
    session=None,
    log_fn=None,
) -> str:
    """Generate one video prompt for one segment."""
    _log = log_fn or (lambda m: None)

    compact_style = _prepare_video_style(video_style)
    style_instruction = ""
    if compact_style:
        style_instruction = f"Video style:\n{compact_style}"

    system = SYSTEM_PROMPT.format(style_instruction=style_instruction)
    user_msg = f"[Segment {segment_id}] {segment_text}"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.5,
    }

    _log(
        f"[api][video] request segment_id={segment_id} model={model_name} "
        f"style={'custom' if style_instruction else 'none'} style_chars={len(compact_style)}"
    )

    resp = _make_request(
        url, payload, headers,
        session=session, log_fn=log_fn,
        log_prefix=f"[api][video][segment {segment_id}] ",
    )

    data = resp.json()
    raw = data["choices"][0]["message"]["content"].strip()
    raw = raw.strip('"').strip("'").strip()

    _log(f"[api][video] done segment_id={segment_id} prompt_chars={len(raw)}")
    return raw


def generate_video_prompts(
    segments: list[dict],
    video_style: str = "",
    model_name: str = "gemini-2.0-flash",
    endpoint: str = "http://localhost:8317/v1",
    api_key: str = "",
    progress_fn=None,
    log_fn=None,
    cancel_check=None,
    on_prompt_saved=None,
    existing_prompts: list[str] | None = None,
) -> list[str]:
    """
    Generate prompts for all segments.

    existing_prompts: continue from already-generated prompts.
    on_prompt_saved: callback(prompts_list) after each prompt is saved.
    """
    _log = log_fn or (lambda m: None)

    if not segments:
        return existing_prompts or []

    base = endpoint.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    url = f"{base}/chat/completions"

    session = _get_session()
    session.headers.update({"Content-Type": "application/json"})
    if api_key and api_key.strip():
        session.headers["Authorization"] = f"Bearer {api_key.strip()}"

    total = len(segments)
    prompts = list(existing_prompts) if existing_prompts else []
    start_index = len(prompts)

    if start_index >= total:
        _log(f"[api][video] already_complete {start_index}/{total}")
        session.close()
        return prompts

    _log(f"[api][video] batch total={total} start={start_index + 1}")
    _log(f"[api][video] endpoint={base} model={model_name} style={video_style or '(none)'}")

    headers = dict(session.headers)

    for i in range(start_index, total):
        if cancel_check and cancel_check():
            _log(f"[api][video] cancelled at segment {i + 1}/{total}")
            break

        seg = segments[i]
        seg_id = seg.get("index", i + 1)

        try:
            prompt = generate_video_prompt_single(
                seg["text"], seg_id, video_style,
                model_name, endpoint, api_key, headers, url,
                session=session, log_fn=log_fn,
            )
            prompts.append(prompt)
        except Exception as e:
            _log(f"[api][video] error segment_id={seg_id}: {e}")
            prompts.append(f"[ERROR] {e}")

        if on_prompt_saved:
            on_prompt_saved(prompts)

        if progress_fn:
            progress_fn(i + 1, total, prompts[-1])

        if i < total - 1:
            if cancel_check and cancel_check():
                _log(f"[api][video] cancelled at segment {i + 1}/{total}")
                break
            _log("[api][video] throttle_wait=2s")
            time.sleep(2)

    session.close()
    ok = sum(1 for p in prompts if not p.startswith("[ERROR]"))
    _log(f"[api][video] completed success={ok}/{total}")

    return prompts
