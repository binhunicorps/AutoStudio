"""
core/content_writer.py
AI writer for content scripts with streaming, retry, and safe logging.
"""

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry as _Retry


MAX_RETRIES = 3
RETRY_DELAY = 3


def _chars_per_word(language: str) -> float:
    """Heuristic chars-per-word (including spaces) for duration guidance."""
    lang = (language or "").strip().lower()
    if lang == "english":
        return 6.0
    if "tiếng việt" in lang or "vietnamese" in lang:
        return 5.2
    if "日本語" in lang or "japanese" in lang or "tiếng nhật" in lang:
        return 2.4
    if "한국어" in lang or "korean" in lang or "tiếng hàn" in lang:
        return 3.0
    return 5.5


def _char_range(value: int, ratio: float = 0.2) -> tuple[int, int]:
    low = max(1, int(value * (1.0 - ratio)))
    high = max(low, int(value * (1.0 + ratio)))
    return low, high


def _get_session() -> requests.Session:
    """Return a reusable session with connection pooling."""
    s = requests.Session()
    adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=_Retry(total=0))
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def write_content(
    title: str,
    style: dict,
    model_name: str,
    endpoint: str = "http://localhost:8317/v1",
    api_key: str = "",
    wpm: int = 130,
    target_sec: float = 8.0,
    duration_minutes: float = 0,
    language: str = "Tiếng Việt",
    on_token=None,
    log_fn=None,
    cancel_check=None,
) -> str:
    """
    Generate content script.
    Output is line-by-line so each line can map to one segment.
    """
    _log = log_fn or (lambda m: None)
    style_name = style.get("name", "General")
    style_prompt = style.get("prompt", "")
    words_per_sent = round((wpm / 60.0) * target_sec)
    cpw = _chars_per_word(language)
    chars_per_sent_est = max(1, int(words_per_sent * cpw))
    chars_per_sent_min, chars_per_sent_max = _char_range(chars_per_sent_est, ratio=0.2)

    if duration_minutes > 0:
        total_words = int(duration_minutes * wpm)
        total_sents = int(duration_minutes * 60 / target_sec)
        total_chars_est = max(1, int(total_words * cpw))
        total_chars_min, total_chars_max = _char_range(total_chars_est, ratio=0.15)
        length_requirement = f"""
YÊU CẦU VỀ ĐỘ DÀI:
- Content dài {duration_minutes} phút
- Khoảng {total_words:,} từ, khoảng {total_sents} câu
- Ước lượng khoảng {total_chars_est:,} ký tự (cho phép ~{total_chars_min:,}-{total_chars_max:,} ký tự)
- Bắt buộc đủ độ dài theo yêu cầu"""
    else:
        length_requirement = "\n6. Viết tối thiểu 20 câu"

    system_prompt = f"""Bạn là người viết nội dung content chuyên nghiệp.

NGÔN NGỮ: Viết toàn bộ bằng {language}.

Style content: {style_name}
Hướng dẫn style:
{style_prompt}

YÊU CẦU BẮT BUỘC VỀ ĐỊNH DẠNG OUTPUT:
1. Mỗi câu/đoạn khoảng {words_per_sent} từ (~{target_sec}s khi đọc ở {wpm} từ/phút)
2. Mỗi câu/đoạn khoảng {chars_per_sent_est} ký tự (cho phép ~{chars_per_sent_min}-{chars_per_sent_max} ký tự)
3. Mỗi câu phải xuống dòng (1 dòng/câu)
4. Không dùng markdown, heading, bullet
5. Không mở đầu kiểu "Dưới đây là..."
{length_requirement}"""

    user_prompt = f"Viết nội dung content bằng {language} về chủ đề: {title}"

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
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "stream": True,
    }

    _log(f"[api][write] request POST {url}")
    _log(f"[api][write] model={model_name} style={style_name} language={language}")
    _log(
        f"[api][write] target per_line~{words_per_sent} words "
        f"(~{chars_per_sent_est} chars, allow {chars_per_sent_min}-{chars_per_sent_max})"
    )
    if duration_minutes and duration_minutes > 0:
        total_words = int(duration_minutes * wpm)
        total_sents = int(duration_minutes * 60 / target_sec)
        total_chars_est = max(1, int(total_words * cpw))
        total_chars_min, total_chars_max = _char_range(total_chars_est, ratio=0.15)
        _log(
            f"[api][write] target total~{duration_minutes}m "
            f"{total_words} words / {total_sents} lines / ~{total_chars_est} chars "
            f"(allow {total_chars_min}-{total_chars_max})"
        )
    _log("[api][write] stream=true prompt_template=content_writer")

    import json as _json

    session = _get_session()
    last_err = None
    resp = None
    attempt = 0
    rate_limit_retries = 0
    max_rate_retries = 10
    start = time.time()

    while attempt < MAX_RETRIES:
        try:
            if cancel_check and cancel_check():
                raise InterruptedError("Cancelled")

            attempt += 1
            start = time.time()
            resp = session.post(url, json=payload, headers=headers, timeout=(15, 60), stream=True)
            elapsed = round(time.time() - start, 1)
            _log(f"[api][write] attempt={attempt} status={resp.status_code} elapsed={elapsed}s")

            if resp.status_code == 429:
                rate_limit_retries += 1
                attempt -= 1
                if rate_limit_retries > max_rate_retries:
                    raise RuntimeError("Rate limit: too many retries (429)")
                wait = 2
                try:
                    err_data = _json.loads(resp.text)
                    details = err_data.get("error", {}).get("details", [])
                    for d in details:
                        if "retryDelay" in d:
                            delay_str = str(d["retryDelay"])
                            wait = max(1, int(float(delay_str.rstrip("s")) + 1))
                            break
                except Exception:
                    pass
                _log(f"[api][write] rate_limit wait={wait}s retry={rate_limit_retries}/{max_rate_retries}")
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                last_err = f"Server {resp.status_code}"
                _log(f"[api][write] server_error status={resp.status_code} elapsed={elapsed}s")
                if attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * attempt
                    _log(f"[api][write] retry_wait={wait}s")
                    time.sleep(wait)
                continue

            resp.raise_for_status()
            break

        except requests.exceptions.ConnectionError as e:
            last_err = f"Connection error: {e}"
            _log(f"[api][write] connection_error: {str(e)[:120]}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            continue

        except requests.exceptions.HTTPError:
            status = resp.status_code if resp is not None else "unknown"
            _log(f"[api][write] http_error status={status}")
            raise RuntimeError(f"Server returned HTTP {status}")

    else:
        raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {last_err}")

    resp.encoding = "utf-8"
    _log("[api][write] connected status=200 streaming=true")

    content_parts = []
    token_count = 0

    for line in resp.iter_lines(decode_unicode=True):
        if cancel_check and cancel_check():
            session.close()
            raise InterruptedError("Cancelled")
        if not line:
            continue
        if not line.startswith("data: "):
            continue

        data_str = line[6:]
        if data_str.strip() == "[DONE]":
            break

        try:
            chunk = _json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            token = delta.get("content", "")
            if token:
                content_parts.append(token)
                token_count += 1
                if on_token:
                    on_token(token)
        except (_json.JSONDecodeError, IndexError, KeyError):
            continue

    session.close()
    elapsed = round(time.time() - start, 1)
    content = "".join(content_parts).strip()
    lines = [l for l in content.split("\n") if l.strip()]

    _log(f"[api][write] done elapsed={elapsed}s")
    _log(
        f"[api][write] output chars={len(content)} lines={len(lines)} "
        f"words={len(content.split())} tokens={token_count}"
    )

    return content
