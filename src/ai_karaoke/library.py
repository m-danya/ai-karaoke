from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .constants import GENIUS_TAG, INSTR_TAG, KARAOKE_TAG, PLAYLISTS_FILE, VOCALS_TAG
from .models import SongPair


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
    name = p.name
    key = stem_base_name(p)
    if key is None:
        return None
    if VOCALS_TAG in name:
        return key, "vocals"
    return key, "instrumental"


def _display_key(rel_parts: Tuple[str, ...], base: str) -> str:
    if not rel_parts:
        return base
    return f"{' - '.join(rel_parts)} - {base}"


def scan_folder(folder: Path) -> List[SongPair]:
    stems: Dict[Tuple[Tuple[str, ...], str], Dict[str, Path]] = {}
    for p in sorted(folder.rglob("*.mp3")):
        if not p.is_file():
            continue
        sk = _stem_key(p)
        if sk is None:
            continue
        key, kind = sk
        rel_dir = p.parent.relative_to(folder)
        rel_parts = () if rel_dir == Path(".") else rel_dir.parts
        stems.setdefault((rel_parts, key), {})[kind] = p

    pairs: List[SongPair] = []
    for (rel_parts, key), kinds in stems.items():
        v = kinds.get("vocals")
        i = kinds.get("instrumental")
        if v and i:
            display = _display_key(rel_parts, key)
            pairs.append(SongPair(key=display, vocals=v, instrumental=i))
    return sorted(pairs, key=lambda p: p.key.casefold())


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


def playlists_path(folder: Path) -> Path:
    return folder / PLAYLISTS_FILE


def normalize_track_id(path: Path | str, *, base_folder: Optional[Path] = None) -> str:
    p = Path(path).expanduser()
    if base_folder is not None and not p.is_absolute():
        p = base_folder / p
    try:
        return str(p.resolve())
    except OSError:
        return str(p.absolute())


def track_id_for_pair(pair: SongPair) -> str:
    return normalize_track_id(pair.vocals)


def _clean_track_ids(raw: object, *, folder: Path) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value:
            continue
        track_id = normalize_track_id(value, base_folder=folder)
        if track_id in seen:
            continue
        seen.add(track_id)
        out.append(track_id)
    return out


def _storage_track_id(track_id: str, *, folder: Path) -> str:
    abs_track = Path(normalize_track_id(track_id, base_folder=folder))
    abs_folder = Path(normalize_track_id(folder))
    try:
        return str(abs_track.relative_to(abs_folder))
    except ValueError:
        try:
            return os.path.relpath(str(abs_track), str(abs_folder))
        except ValueError:
            return str(abs_track)


def _to_storage_track_ids(track_ids: List[str], *, folder: Path) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for track_id in _clean_track_ids(track_ids, folder=folder):
        storage_id = _storage_track_id(track_id, folder=folder)
        if storage_id in seen:
            continue
        seen.add(storage_id)
        out.append(storage_id)
    return out


def load_playlists(folder: Path) -> Tuple[Dict[str, List[str]], List[str]]:
    path = playlists_path(folder)
    if not path.exists():
        return {}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, []
    if not isinstance(raw, dict):
        return {}, []

    playlists: Dict[str, List[str]] = {}
    raw_playlists = raw.get("playlists")
    if isinstance(raw_playlists, dict):
        for raw_name, raw_items in raw_playlists.items():
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            playlists[name] = _clean_track_ids(raw_items, folder=folder)

    history = _clean_track_ids(raw.get("history"), folder=folder)
    return playlists, history


def save_playlists(folder: Path, playlists: Dict[str, List[str]], history: List[str]) -> None:
    path = playlists_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "playlists": {
            name: _to_storage_track_ids(items, folder=folder)
            for name, items in playlists.items()
            if name.strip()
        },
        "history": _to_storage_track_ids(history, folder=folder),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
