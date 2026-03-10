"""
core/splitter.py
Thuật toán tách nội dung content thành các đoạn ~8 giây.
"""

import re

_RE_NEWLINE   = re.compile(r'\r\n|\r')
_RE_SPACES    = re.compile(r'[ \t]+')
_RE_SENTENCE  = re.compile(r'(?<=[.!?])\s+|(?<=\.\.\.)\s+')


def estimate_duration(text: str, wpm: int = 130) -> float:
    """Ước tính thời gian đọc (giây) cho một đoạn text."""
    words = len(text.split())
    return (words / wpm) * 60.0


def split_into_sentences(text: str) -> list[str]:
    """
    Tách văn bản thành danh sách câu.
    Hỗ trợ tiếng Việt và tiếng Anh.
    """
    text = _RE_NEWLINE.sub('\n', text)
    text = _RE_SPACES.sub(' ', text)
    sentences = _RE_SENTENCE.split(text)
    return [s.strip() for s in sentences if s.strip()]


def split_content(text: str, wpm: int = 130, target_seconds: float = 8.0,
                  flex_seconds: float = 3.0) -> list[dict]:
    """
    Tách toàn bộ nội dung content thành các đoạn ngắn.

    Args:
        text:           Toàn bộ nội dung content.
        wpm:            Tốc độ đọc (từ/phút), mặc định 130.
        target_seconds: Thời gian mục tiêu mỗi đoạn (giây), mặc định 8.
        flex_seconds:   Độ linh hoạt cho phép (±giây).

    Returns:
        Danh sách dict: {
            'index':     số thứ tự (bắt đầu từ 1),
            'text':      nội dung đoạn,
            'words':     số từ,
            'duration':  thời gian đọc ước tính (giây, làm tròn 1 chữ số)
        }
    """
    sentences = split_into_sentences(text)
    if not sentences:
        return []

    segments = []
    current_sentences = []
    current_words = 0
    words_per_segment = (wpm / 60.0) * target_seconds  # từ / đoạn

    for sentence in sentences:
        sentence_words = len(sentence.split())

        if not current_sentences:
            # Đoạn đang rỗng, thêm câu vào dù câu có dài
            current_sentences.append(sentence)
            current_words += sentence_words
        else:
            projected_words = current_words + sentence_words
            projected_duration = (projected_words / wpm) * 60.0
            max_duration = target_seconds + flex_seconds

            if projected_duration <= max_duration:
                # Còn trong ngưỡng cho phép → thêm vào đoạn hiện tại
                current_sentences.append(sentence)
                current_words += sentence_words
            else:
                # Vượt ngưỡng → đóng đoạn hiện tại, bắt đầu đoạn mới
                segment_text = ' '.join(current_sentences)
                segments.append({
                    'index': len(segments) + 1,
                    'text': segment_text,
                    'words': current_words,
                    'duration': round((current_words / wpm) * 60.0, 1)
                })
                current_sentences = [sentence]
                current_words = sentence_words

    # Đoạn cuối chưa được đóng
    if current_sentences:
        segment_text = ' '.join(current_sentences)
        segments.append({
            'index': len(segments) + 1,
            'text': segment_text,
            'words': current_words,
            'duration': round((current_words / wpm) * 60.0, 1)
        })

    return segments


def get_summary(segments: list[dict]) -> dict:
    """Thống kê tổng quan về kết quả tách."""
    if not segments:
        return {"count": 0, "total_words": 0, "total_duration": 0, "avg_duration": 0}
    total_words = sum(s['words'] for s in segments)
    total_duration = sum(s['duration'] for s in segments)
    n = len(segments)
    return {
        "count": n,
        "total_segments": n,
        "total_words": total_words,
        "total_duration": round(total_duration, 1),
        "total_duration_seconds": round(total_duration, 1),
        "avg_duration": round(total_duration / n, 1),
        "avg_duration_seconds": round(total_duration / n, 1),
    }
