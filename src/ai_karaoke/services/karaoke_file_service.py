from __future__ import annotations

import json
from pathlib import Path

from ..library_paths import karaoke_path_for_pair
from ..models import KaraokeEntry, KaraokeWord, SongPair


def clean_lyrics_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.startswith("["):
            continue
        lines.append(cleaned)
    return lines


def load_karaoke_entries_for_pair(pair: SongPair) -> list[KaraokeEntry]:
    return load_karaoke_entries(karaoke_path_for_pair(pair))


def load_karaoke_entries(path: Path) -> list[KaraokeEntry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Karaoke file should be a list: {path}")

    raw_entries: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        line = item.get("line")
        end_ts = item.get("end_ts")
        if not isinstance(line, str):
            continue
        try:
            end_val = float(end_ts)
        except (TypeError, ValueError):
            continue
        raw_entries.append(
            {
                "line": line,
                "end_ts": end_val,
                "start_ts": item.get("start_ts"),
                "words": item.get("words"),
            }
        )

    if not raw_entries:
        raise ValueError(f"No valid entries found in: {path}")

    raw_entries.sort(key=lambda item: float(item["end_ts"]))
    entries: list[KaraokeEntry] = []
    prev_end = 0.0
    for item in raw_entries:
        end_val = float(item["end_ts"])
        if end_val < prev_end:
            end_val = prev_end

        start_raw = item.get("start_ts")
        if start_raw is None:
            start_val = prev_end
        else:
            try:
                start_val = float(start_raw)
            except (TypeError, ValueError):
                start_val = prev_end
        if start_val < prev_end:
            start_val = prev_end
        if start_val > end_val:
            start_val = end_val

        words = parse_karaoke_words(item.get("words"), line_start=start_val, line_end=end_val)
        entries.append(
            {
                "line": str(item["line"]),
                "start_ts": start_val,
                "end_ts": end_val,
                "words": words,
            }
        )
        prev_end = end_val
    return entries


def parse_karaoke_words(
    raw_words: object,
    *,
    line_start: float,
    line_end: float,
) -> list[KaraokeWord]:
    if not isinstance(raw_words, list):
        return []

    words: list[KaraokeWord] = []
    prev_end = line_start
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        raw_word = item.get("word")
        if not isinstance(raw_word, str):
            continue
        word = raw_word.strip()
        if not word:
            continue

        start_raw = item.get("start_ts", prev_end)
        end_raw = item.get("end_ts", start_raw)
        try:
            start_ts = float(start_raw)
        except (TypeError, ValueError):
            start_ts = prev_end
        try:
            end_ts = float(end_raw)
        except (TypeError, ValueError):
            end_ts = start_ts

        if start_ts < prev_end:
            start_ts = prev_end
        if end_ts < start_ts:
            end_ts = start_ts
        if start_ts > line_end:
            start_ts = line_end
        if end_ts > line_end:
            end_ts = line_end

        words.append({"word": word, "start_ts": start_ts, "end_ts": end_ts})
        prev_end = end_ts
    return words
