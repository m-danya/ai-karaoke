from __future__ import annotations

from bisect import bisect_right
from typing import Sequence

from ..models import KaraokeEntry, KaraokeFrameState


def karaoke_end_timestamps(entries: Sequence[KaraokeEntry]) -> list[float]:
    return [float(entry["end_ts"]) for entry in entries]


def empty_karaoke_frame_state(visible_lines: int, *, active_slot: int = 0) -> KaraokeFrameState:
    count = max(1, int(visible_lines))
    normalized_slot = max(0, int(active_slot)) % count
    return KaraokeFrameState(
        slot_lines=tuple("" for _ in range(count)),
        active_slot=normalized_slot,
        words=None,
        sung_words=0,
        active_word_idx=None,
        active_word_progress=0.0,
    )


def countdown_frame_state(visible_lines: int, value: int) -> KaraokeFrameState:
    state = empty_karaoke_frame_state(visible_lines, active_slot=max(1, int(visible_lines)) // 2)
    slot_lines = list(state.slot_lines)
    slot_lines[state.active_slot] = f"{int(value)}.."
    return KaraokeFrameState(
        slot_lines=tuple(slot_lines),
        active_slot=state.active_slot,
        words=None,
        sung_words=0,
        active_word_idx=None,
        active_word_progress=0.0,
    )


def finish_frame_state(visible_lines: int, message: str) -> KaraokeFrameState:
    state = empty_karaoke_frame_state(visible_lines, active_slot=max(1, int(visible_lines)) // 2)
    slot_lines = list(state.slot_lines)
    slot_lines[state.active_slot] = str(message)
    return KaraokeFrameState(
        slot_lines=tuple(slot_lines),
        active_slot=state.active_slot,
        words=None,
        sung_words=0,
        active_word_idx=None,
        active_word_progress=0.0,
    )


def karaoke_frame_state(
    entries: Sequence[KaraokeEntry],
    *,
    visible_lines: int,
    t: float,
    end_timestamps: Sequence[float] | None = None,
) -> KaraokeFrameState:
    count = max(1, int(visible_lines))
    if not entries:
        return empty_karaoke_frame_state(count)

    timeline = end_timestamps if end_timestamps is not None else karaoke_end_timestamps(entries)
    idx = bisect_right(timeline, float(t))
    if idx >= len(entries):
        return empty_karaoke_frame_state(count, active_slot=idx % count)

    active_slot = idx % count
    slot_lines = [""] * count
    total = len(entries)
    for slot_idx in range(count):
        line_idx = idx + ((slot_idx - active_slot) % count)
        if line_idx < total:
            slot_lines[slot_idx] = str(entries[line_idx]["line"])

    entry = entries[idx]
    words = entry["words"]
    if not words:
        return KaraokeFrameState(
            slot_lines=tuple(slot_lines),
            active_slot=active_slot,
            words=None,
            sung_words=0,
            active_word_idx=None,
            active_word_progress=0.0,
        )

    word_end_ts = [float(word["end_ts"]) for word in words]
    sung_words = bisect_right(word_end_ts, float(t))
    active_word_idx: int | None = None
    for word_idx, word in enumerate(words):
        if float(word["start_ts"]) <= t < float(word["end_ts"]):
            active_word_idx = word_idx
            break

    if active_word_idx is None and sung_words < len(words) and t >= float(words[sung_words]["start_ts"]):
        active_word_idx = sung_words

    active_word_progress = 0.0
    if active_word_idx is not None:
        word = words[active_word_idx]
        word_start = float(word["start_ts"])
        word_end = float(word["end_ts"])
        if word_end <= word_start:
            active_word_progress = 1.0 if t >= word_end else 0.0
        else:
            active_word_progress = min(max((t - word_start) / (word_end - word_start), 0.0), 1.0)

    return KaraokeFrameState(
        slot_lines=tuple(slot_lines),
        active_slot=active_slot,
        words=tuple(str(word["word"]) for word in words),
        sung_words=sung_words,
        active_word_idx=active_word_idx,
        active_word_progress=active_word_progress,
    )
