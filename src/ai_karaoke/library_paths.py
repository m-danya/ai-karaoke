from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .constants import GENIUS_TAG, KARAOKE_TAG
from .models import SongPair


def stem_base_name(path: Path) -> Optional[str]:
    name = path.name
    if not name.lower().endswith(".mp3"):
        return None

    from .constants import INSTR_TAG, VOCALS_TAG

    if VOCALS_TAG in name:
        return name.replace(VOCALS_TAG, "").rsplit(".", 1)[0]
    if INSTR_TAG in name:
        return name.replace(INSTR_TAG, "").rsplit(".", 1)[0]
    return None


def karaoke_path_for_pair(pair: SongPair) -> Path:
    base = base_name_for_pair(pair)
    return pair.vocals.with_name(f"{base}{KARAOKE_TAG}.json")


def genius_lyrics_path_for_pair(pair: SongPair) -> Path:
    base = base_name_for_pair(pair)
    return pair.vocals.with_name(f"{base}{GENIUS_TAG}.txt")


def base_name_for_pair(pair: SongPair) -> str:
    base = stem_base_name(pair.vocals)
    if base is None:
        raise ValueError(f"Unsupported vocals path: {pair.vocals}")
    return base


def normalize_track_id(path: Path | str, *, base_folder: Path | None = None) -> str:
    track_path = Path(path).expanduser()
    if base_folder is not None and not track_path.is_absolute():
        track_path = base_folder / track_path
    try:
        return str(track_path.resolve())
    except OSError:
        return str(track_path.absolute())


def track_id_for_pair(pair: SongPair) -> str:
    return normalize_track_id(pair.vocals)


def storage_track_id(track_id: str, *, folder: Path) -> str:
    abs_track = Path(normalize_track_id(track_id, base_folder=folder))
    abs_folder = Path(normalize_track_id(folder))
    try:
        return str(abs_track.relative_to(abs_folder))
    except ValueError:
        try:
            return os.path.relpath(str(abs_track), str(abs_folder))
        except ValueError:
            return str(abs_track)
