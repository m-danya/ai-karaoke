from __future__ import annotations

import shutil
from pathlib import Path

from ..audio import transpose_mp3
from ..library_paths import base_name_for_pair, genius_lyrics_path_for_pair, karaoke_path_for_pair
from ..models import SongPair, TransposedTrackPaths
from ..constants import GENIUS_TAG, INSTR_TAG, KARAOKE_TAG, VOCALS_TAG


def transpose_suffix(semitones: int, attempt: int = 0) -> str:
    if attempt <= 0:
        return f"({semitones:+d} transposed)"
    return f"({semitones:+d} transposed {attempt + 1})"


def build_transposed_track_paths(
    pair: SongPair,
    semitones: int,
    attempt: int = 0,
) -> TransposedTrackPaths:
    base_name = f"{base_name_for_pair(pair)} {transpose_suffix(semitones, attempt)}"
    return TransposedTrackPaths(
        base_name=base_name,
        vocals=pair.vocals.with_name(f"{base_name}{VOCALS_TAG}.mp3"),
        instrumental=pair.instrumental.with_name(f"{base_name}{INSTR_TAG}.mp3"),
        genius_lyrics=pair.vocals.with_name(f"{base_name}{GENIUS_TAG}.txt"),
        karaoke=pair.vocals.with_name(f"{base_name}{KARAOKE_TAG}.json"),
    )


def find_available_transposed_paths(pair: SongPair, semitones: int) -> TransposedTrackPaths:
    attempt = 0
    while True:
        paths = build_transposed_track_paths(pair, semitones, attempt)
        targets = (paths.vocals, paths.instrumental, paths.genius_lyrics, paths.karaoke)
        if not any(path.exists() for path in targets):
            return paths
        attempt += 1


def copy_related_file_if_present(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    shutil.copy2(source, target)
    return True


def transpose_track_copy(pair: SongPair, semitones: int) -> TransposedTrackPaths:
    paths = find_available_transposed_paths(pair, semitones)
    created_paths: list[Path] = []
    try:
        transpose_mp3(pair.vocals, paths.vocals, semitones)
        created_paths.append(paths.vocals)

        transpose_mp3(pair.instrumental, paths.instrumental, semitones)
        created_paths.append(paths.instrumental)

        if copy_related_file_if_present(genius_lyrics_path_for_pair(pair), paths.genius_lyrics):
            created_paths.append(paths.genius_lyrics)
        if copy_related_file_if_present(karaoke_path_for_pair(pair), paths.karaoke):
            created_paths.append(paths.karaoke)
        return paths
    except Exception:
        for path in reversed(created_paths):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                pass
        raise
