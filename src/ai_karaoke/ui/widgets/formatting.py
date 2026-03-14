from __future__ import annotations


def format_time(sec: float) -> str:
    sec = max(0.0, sec)
    minutes = int(sec // 60)
    seconds = int(sec % 60)
    return f"{minutes:02d}:{seconds:02d}"
