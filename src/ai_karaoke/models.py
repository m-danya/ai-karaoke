from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


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
