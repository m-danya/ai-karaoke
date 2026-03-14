from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing as mp
from pathlib import Path

from tqdm import tqdm

from ..services.karaoke_file_service import clean_lyrics_lines
from .common import DEFAULT_JOBS
from .io_paths import collect_genius_lyrics_files, find_vocals_for_lyrics, karaoke_output_path
from .lyrics_align import LyricsAligner, build_karaoke_entries


_WORKER_ALIGNER: LyricsAligner | None = None
_SPAWN_CTX = mp.get_context("spawn")


def get_worker_aligner() -> LyricsAligner:
    global _WORKER_ALIGNER
    if _WORKER_ALIGNER is None:
        _WORKER_ALIGNER = LyricsAligner()
    return _WORKER_ALIGNER


def align_one_lyrics(lyrics_path_raw: str, force: bool = False) -> list[str]:
    lyrics_path = Path(lyrics_path_raw)
    warnings: list[str] = []

    vocals_path = find_vocals_for_lyrics(lyrics_path)
    if vocals_path is None:
        return [f"Warning: vocals not found for {lyrics_path}"]

    output_path = karaoke_output_path(vocals_path)
    if output_path.exists() and not force:
        try:
            if output_path.stat().st_mtime >= lyrics_path.stat().st_mtime:
                return warnings
        except OSError:
            pass

    try:
        raw = lyrics_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"Warning: could not read {lyrics_path}: {exc}"]

    lines = clean_lyrics_lines(raw)
    if not lines:
        return [f"Warning: no usable lyric lines in {lyrics_path}"]

    full_text = " ".join(lines)
    if not full_text:
        return [f"Warning: empty lyric text in {lyrics_path}"]

    aligner = get_worker_aligner()
    try:
        word_segments = aligner.align_word_segments(vocals_path, full_text)
    except Exception as exc:  # noqa: BLE001
        return [f"Warning: alignment failed for {lyrics_path}: {exc}"]

    if not word_segments:
        return [f"Warning: alignment produced no segments for {lyrics_path}"]

    expected_words = sum(len(line.split()) for line in lines)
    if expected_words != len(word_segments):
        warnings.append(
            "Warning: word count mismatch for "
            f"{lyrics_path} (expected {expected_words}, got {len(word_segments)})"
        )

    entries = build_karaoke_entries(lines, word_segments)
    try:
        output_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        warnings.append(f"Warning: could not write {output_path}: {exc}")
    return warnings


def process_genius_lyrics(root: Path, force: bool = False, jobs: int = DEFAULT_JOBS) -> None:
    files = collect_genius_lyrics_files(root)
    if not files:
        print("No Genius lyrics files found.")
        return

    max_workers = min(jobs, len(files))
    if max_workers == 1:
        for lyrics_path in tqdm(files, desc="Aligning lyrics", unit="file"):
            try:
                warnings = align_one_lyrics(str(lyrics_path), force=force)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: alignment failed for {lyrics_path}: {exc}")
                continue
            for warning in warnings:
                print(warning)
        return

    with ProcessPoolExecutor(max_workers=max_workers, mp_context=_SPAWN_CTX) as executor:
        futures = {
            executor.submit(align_one_lyrics, str(lyrics_path), force): lyrics_path
            for lyrics_path in files
        }
        for future in tqdm(
            as_completed(futures), total=len(futures), desc="Aligning lyrics", unit="file"
        ):
            lyrics_path = futures[future]
            try:
                warnings = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: alignment failed for {lyrics_path}: {exc}")
                continue
            for warning in warnings:
                print(warning)
