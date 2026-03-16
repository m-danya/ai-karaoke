from __future__ import annotations

from pathlib import Path

from ai_karaoke.music_processing.genius_fetch import GENIUS_LYRICS_SUFFIX, VOCALS_SUFFIX


INSTRUMENTAL_SUFFIX = "_(Instrumental)"
RESULT_SUFFIXES = (INSTRUMENTAL_SUFFIX, VOCALS_SUFFIX)
KARAOKE_LYRICS_SUFFIX = "_(Karaoke Lyrics)"


def is_result_mp3(path: Path) -> bool:
    return path.suffix.lower() == ".mp3" and path.stem.endswith(RESULT_SUFFIXES)


def collect_mp3_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if root.is_file():
        return [root] if root.suffix.lower() == ".mp3" and not is_result_mp3(root) else []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".mp3" and not is_result_mp3(path)
    )


def is_genius_lyrics_txt(path: Path) -> bool:
    return path.suffix.lower() == ".txt" and path.stem.endswith(GENIUS_LYRICS_SUFFIX)


def collect_genius_lyrics_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if root.is_file():
        return [root] if is_genius_lyrics_txt(root) else []
    return sorted(path for path in root.rglob("*") if path.is_file() and is_genius_lyrics_txt(path))


def karaoke_output_path(vocals_path: Path) -> Path:
    base = vocals_path.stem
    if base.endswith(VOCALS_SUFFIX):
        base = base[: -len(VOCALS_SUFFIX)]
    return vocals_path.with_name(f"{base}{KARAOKE_LYRICS_SUFFIX}.json")


def find_vocals_for_lyrics(lyrics_path: Path) -> Path | None:
    stem = lyrics_path.stem
    if not stem.endswith(GENIUS_LYRICS_SUFFIX):
        return None
    base = stem[: -len(GENIUS_LYRICS_SUFFIX)]
    candidates = [base]
    if base.endswith("_"):
        candidates.append(base[:-1])
    for candidate in candidates:
        vocals = lyrics_path.with_name(f"{candidate}{VOCALS_SUFFIX}.mp3")
        if vocals.exists():
            return vocals
    return None


def processing_root_after_separation(path: Path) -> Path:
    if path.exists():
        return path
    if path.suffix.lower() == ".mp3":
        return path.parent
    return path
