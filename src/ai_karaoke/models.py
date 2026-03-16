from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict


@dataclass(frozen=True)
class SongPair:
    key: str
    vocals: Path
    instrumental: Path


class KaraokeWord(TypedDict):
    word: str
    start_ts: float
    end_ts: float


class KaraokeEntry(TypedDict):
    line: str
    start_ts: float
    end_ts: float
    words: list[KaraokeWord]


@dataclass(frozen=True)
class TrackListItem:
    track_id: str
    label: str
    pair: SongPair | None
    missing: bool


@dataclass(frozen=True)
class TransposedTrackPaths:
    base_name: str
    vocals: Path
    instrumental: Path
    genius_lyrics: Path
    karaoke: Path


@dataclass(frozen=True)
class ExportMixSettings:
    label: str
    vocals_gain: float
    instr_gain: float
    vocals_muted: bool
    instr_muted: bool


VideoAspectRatio = Literal["16:9", "ultrawide", "custom"]


@dataclass(frozen=True)
class VideoExportSettings:
    label: str
    aspect_ratio: str
    width: int
    height: int
    fps: int


@dataclass(frozen=True)
class KaraokeRenderSettings:
    font_size: int
    visible_lines: int
    countdown_enabled: bool
    finish_celebration_enabled: bool
    tk_scaling: float = 1.0
    lyrics_font_family: str = "Playfair Display"
    title_font_family: str = "Fira Sans"
    footer_font_family: str = "Fira Sans"


@dataclass(frozen=True)
class KaraokeFrameState:
    slot_lines: tuple[str, ...]
    active_slot: int
    words: tuple[str, ...] | None
    sung_words: int
    active_word_idx: int | None
    active_word_progress: float
