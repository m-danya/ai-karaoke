from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from .constants import INSTR_TAG, VOCALS_TAG
from .library_paths import (
    base_name_for_pair,
    genius_lyrics_path_for_pair,
    karaoke_path_for_pair,
    normalize_track_id,
    track_id_for_pair,
)
from .library_scan import display_key as _scan_display_key, scan_folder, stem_key as _scan_stem_key
from .playlist_store import load_playlists, playlists_path, save_playlists


def stem_base_name(path: Path) -> Optional[str]:
    name = path.name
    if not name.lower().endswith(".mp3"):
        return None
    if VOCALS_TAG in name:
        return name.replace(VOCALS_TAG, "").rsplit(".", 1)[0]
    if INSTR_TAG in name:
        return name.replace(INSTR_TAG, "").rsplit(".", 1)[0]
    return None


def _stem_key(p: Path) -> Optional[Tuple[str, str]]:
    """
    Returns (key, kind) where kind is "vocals" or "instrumental".
    Key is filename without the matching suffix and extension.
    """
    return _scan_stem_key(p)


def _display_key(rel_parts: Tuple[str, ...], base: str) -> str:
    return _scan_display_key(rel_parts, base)
