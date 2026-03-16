from __future__ import annotations

from pathlib import Path

from .alignment_pipeline import process_genius_lyrics
from .genius_fetch import fetch_missing_genius_lyrics
from .io_paths import processing_root_after_separation
from .separation import separate_mp3s


def run_pipeline(path: Path, *, genius_delay_seconds: float, jobs: int) -> None:
    separate_mp3s(path, jobs=jobs)
    root = processing_root_after_separation(path)
    fetch_missing_genius_lyrics(root, genius_delay_seconds)
    process_genius_lyrics(root, jobs=jobs)


def run_align_only(path: Path, *, jobs: int) -> None:
    process_genius_lyrics(path, force=True, jobs=jobs)
