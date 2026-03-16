from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .constants import PLAYLISTS_FILE
from .library_paths import normalize_track_id, storage_track_id


def playlists_path(folder: Path) -> Path:
    return folder / PLAYLISTS_FILE


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
            playlists[name] = clean_track_ids(raw_items, folder=folder)

    history = clean_track_ids(raw.get("history"), folder=folder)
    return playlists, history


def save_playlists(folder: Path, playlists: Dict[str, List[str]], history: List[str]) -> None:
    path = playlists_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "playlists": {
            name: to_storage_track_ids(items, folder=folder)
            for name, items in playlists.items()
            if name.strip()
        },
        "history": to_storage_track_ids(history, folder=folder),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_track_ids(raw: object, *, folder: Path) -> List[str]:
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


def to_storage_track_ids(track_ids: List[str], *, folder: Path) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for track_id in clean_track_ids(track_ids, folder=folder):
        storage_id = storage_track_id(track_id, folder=folder)
        if storage_id in seen:
            continue
        seen.add(storage_id)
        out.append(storage_id)
    return out
