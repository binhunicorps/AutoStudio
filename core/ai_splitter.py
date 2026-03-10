"""
core/ai_splitter.py
Tách đoạn content thông minh qua OpenAI-compatible endpoint (Proxypal).
Dùng requests trực tiếp — không qua openai SDK.
"""

import json
import re

import requests


SYSTEM_PROMPT = """Bạn là chuyên gia biên tập script video. Nhiệm vụ của bạn là tách một đoạn nội dung content thành các phần nhỏ phù hợp để đọc trong video.

Quy tắc tách đoạn:
1. Mỗi đoạn phải có thời gian đọc XẤP XỈ {target_seconds} giây (tốc độ đọc: {wpm} từ/phút, tương đương ~{words_per_segment} từ/đoạn).
2. CHỈ được cắt tại điểm tự nhiên: sau dấu câu (. ! ?), sau mệnh đề hoàn chỉnh, hoặc tại điểm chuyển ý.
3. KHÔNG được cắt giữa một cụm từ, một mệnh đề quan trọng, hoặc giữa chủ ngữ và vị ngữ.
4. Nếu một câu quá dài, hãy tìm dấu phẩy hoặc liên từ (và, nhưng, tuy nhiên, vì vậy, hoặc, còn) để cắt tại đó.
5. Mỗi đoạn phải có ít nhất 5 từ.
6. Giữ nguyên 100% nội dung gốc, KHÔNG thêm hoặc bớt từ nào.

Trả về KẾT QUẢ DUY NHẤT là một JSON array các chuỗi:
["đoạn 1 nguyên văn", "đoạn 2 nguyên văn", ...]

KHÔNG giải thích, KHÔNG có text ngoài JSON."""


def _build_base_url(endpoint: str) -> str:
    """Chuẩn hóa endpoint về dạng http://host:port/v1"""
    base = endpoint.rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base


def _make_headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


def fetch_models(endpoint: str, api_key: str = "") -> list[str]:
    """Lấy danh sách model từ /v1/models. Trả về list model id."""
    base = _build_base_url(endpoint)
    url = f"{base}/models"
    resp = requests.get(url, headers=_make_headers(api_key), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    models = [m["id"] for m in data.get("data", [])]
    return sorted(models)


def split_content_ai(
    text: str,
    model_name: str = "gemini-2.0-flash",
    wpm: int = 130,
    target_seconds: float = 8.0,
    endpoint: str = "http://localhost:8317/v1",
    api_key: str = "",
    cancel_check=None,
) -> list[dict]:
    """
    Tách content qua OpenAI-compatible endpoint.

    Returns:
        list[dict]: [{'index', 'text', 'words', 'duration'}, ...]
    """
    words_per_segment = round((wpm / 60.0) * target_seconds, 1)

    system_instruction = SYSTEM_PROMPT.format(
        target_seconds=target_seconds,
        wpm=wpm,
        words_per_segment=words_per_segment,
    )

    base = _build_base_url(endpoint)
    url = f"{base}/chat/completions"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user",   "content": text},
        ],
        "temperature": 0.2,
    }

    try:
        if cancel_check and cancel_check():
            raise InterruptedError("Cancelled")
        resp = requests.post(
            url,
            json=payload,
            headers=_make_headers(api_key),
            timeout=(10, 120),
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise ConnectionError(f"Không kết nối được tới {url}\nKiểm tra Proxypal đang chạy.")
    except requests.exceptions.HTTPError:
        raise RuntimeError(f"Server trả về lỗi {resp.status_code}:\n{resp.text[:400]}")

    if cancel_check and cancel_check():
        raise InterruptedError("Cancelled")

    data = resp.json()
    raw = data["choices"][0]["message"]["content"].strip()

    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"AI trả về kết quả không hợp lệ.\n{e}\n\nResponse:\n{raw[:400]}") from e

    if isinstance(parsed, list):
        segments_text = parsed
    elif isinstance(parsed, dict):
        segments_text = next((v for v in parsed.values() if isinstance(v, list)), None)
        if segments_text is None:
            raise ValueError("AI không trả về danh sách đoạn.")
    else:
        raise ValueError("AI không trả về dạng danh sách.")

    segments = []
    for i, seg_text in enumerate(segments_text):
        seg_text = str(seg_text).strip()
        if not seg_text:
            continue
        words = len(seg_text.split())
        duration = round((words / wpm) * 60.0, 1)
        segments.append({"index": i + 1, "text": seg_text, "words": words, "duration": duration})

    return segments
