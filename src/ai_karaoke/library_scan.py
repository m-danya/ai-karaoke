from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .constants import INSTR_TAG, VOCALS_TAG
from .library_paths import stem_base_name
from .models import SongPair


def stem_key(path: Path) -> Optional[Tuple[str, str]]:
    name = path.name
    key = stem_base_name(path)
    if key is None:
        return None
    if VOCALS_TAG in name:
        return key, "vocals"
    if INSTR_TAG in name:
        return key, "instrumental"
    return None


def display_key(rel_parts: Tuple[str, ...], base: str) -> str:
    if not rel_parts:
        return base
    return f"{' - '.join(rel_parts)} - {base}"


def scan_folder(folder: Path) -> List[SongPair]:
    stems: Dict[Tuple[Tuple[str, ...], str], Dict[str, Path]] = {}
    for path in sorted(folder.rglob("*.mp3")):
        if not path.is_file():
            continue
        pair_key = stem_key(path)
        if pair_key is None:
            continue
        key, kind = pair_key
        rel_dir = path.parent.relative_to(folder)
        rel_parts = () if rel_dir == Path(".") else rel_dir.parts
        stems.setdefault((rel_parts, key), {})[kind] = path

    pairs: List[SongPair] = []
    for (rel_parts, key), kinds in stems.items():
        vocals = kinds.get("vocals")
        instrumental = kinds.get("instrumental")
        if vocals and instrumental:
            pairs.append(
                SongPair(
                    key=display_key(rel_parts, key),
                    vocals=vocals,
                    instrumental=instrumental,
                )
            )
    return sorted(pairs, key=lambda pair: pair.key.casefold())
