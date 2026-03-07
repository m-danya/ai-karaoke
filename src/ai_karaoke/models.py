from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SongPair:
    key: str
    vocals: Path
    instrumental: Path
