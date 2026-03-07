#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import zipfile

from audio_separator.separator import Separator
from tqdm import tqdm

from ai_karaoke.music_processing.genius_fetch import (
    DEFAULT_GENIUS_DELAY_SECONDS,
    GENIUS_LYRICS_SUFFIX,
    VOCALS_SUFFIX,
    fetch_missing_genius_lyrics,
)
from ai_karaoke.music_processing.lyrics_align import (
    LyricsAligner,
    build_karaoke_entries,
    clean_lyrics_lines,
)


INSTRUMENTAL_SUFFIX = "_(Instrumental)"
RESULT_SUFFIXES = (INSTRUMENTAL_SUFFIX, VOCALS_SUFFIX)
KARAOKE_LYRICS_SUFFIX = "_(Karaoke Lyrics)"
MODEL_FILENAME = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
_IN_PACKAGE_MODEL_DATA_DIR = (
    Path(__file__).resolve().parents[1] / "audio-separator-script" / "models-data"
)
_LEGACY_MODEL_DATA_DIR = (
    Path(__file__).resolve().parents[3] / "audio-separator-script" / "models-data"
)
MODEL_DATA_DIR = (
    _IN_PACKAGE_MODEL_DATA_DIR
    if _IN_PACKAGE_MODEL_DATA_DIR.exists()
    else (
        _LEGACY_MODEL_DATA_DIR
        if _LEGACY_MODEL_DATA_DIR.exists()
        else Path(__file__).resolve().parent / "models-data"
    )
)
DEFAULT_JOBS = 1
MDX_PARAMS = {
    "hop_length": 1024,
    "segment_size": 256,
    "overlap": 0.2,
    "batch_size": 1,
    "enable_denoise": False,
}

_WORKER_SEPARATOR: Separator | None = None
_WORKER_ALIGNER: LyricsAligner | None = None


def _is_result_mp3(path: Path) -> bool:
    return path.suffix.lower() == ".mp3" and path.stem.endswith(RESULT_SUFFIXES)


def collect_mp3_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if root.is_file():
        return [root] if root.suffix.lower() == ".mp3" and not _is_result_mp3(root) else []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() == ".mp3" and not _is_result_mp3(p)
    )


def _is_genius_lyrics_txt(path: Path) -> bool:
    return path.suffix.lower() == ".txt" and path.stem.endswith(GENIUS_LYRICS_SUFFIX)


def collect_genius_lyrics_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if root.is_file():
        return [root] if _is_genius_lyrics_txt(root) else []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and _is_genius_lyrics_txt(p)
    )


def _karaoke_output_path(vocals_path: Path) -> Path:
    base = vocals_path.stem
    if base.endswith(VOCALS_SUFFIX):
        base = base[: -len(VOCALS_SUFFIX)]
    return vocals_path.with_name(f"{base}{KARAOKE_LYRICS_SUFFIX}.json")


def _find_vocals_for_lyrics(lyrics_path: Path) -> Path | None:
    stem = lyrics_path.stem
    if not stem.endswith(GENIUS_LYRICS_SUFFIX):
        return None
    base = stem[: -len(GENIUS_LYRICS_SUFFIX)]
    candidates = [base]
    if base.endswith("_"):
        candidates.append(base[:-1])
    for cand in candidates:
        vocals = lyrics_path.with_name(f"{cand}{VOCALS_SUFFIX}.mp3")
        if vocals.exists():
            return vocals
    return None


def _processing_root_after_separation(path: Path) -> Path:
    if path.exists():
        return path
    if path.suffix.lower() == ".mp3":
        return path.parent
    return path


def _is_valid_torch_checkpoint(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            # Torch checkpoints are zip archives; an empty name list is a bad sign.
            return bool(archive.namelist())
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


def _prepare_model_cache(model_dir: Path, model_filename: str) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / model_filename
    if not model_path.exists():
        return
    if _is_valid_torch_checkpoint(model_path):
        return

    try:
        size = model_path.stat().st_size
    except OSError:
        size = None
    size_suffix = f", size={size} bytes" if size is not None else ""
    print(
        "Warning: cached model appears corrupted, deleting for re-download: "
        f"{model_path}{size_suffix}"
    )
    model_path.unlink(missing_ok=True)


def _get_worker_separator() -> Separator:
    global _WORKER_SEPARATOR
    if _WORKER_SEPARATOR is None:
        _WORKER_SEPARATOR = Separator(
            output_format="MP3",
            mdx_params=MDX_PARAMS,
            model_file_dir=str(MODEL_DATA_DIR),
        )
        _WORKER_SEPARATOR.load_model(model_filename=MODEL_FILENAME)
    return _WORKER_SEPARATOR


def _separate_one_mp3(mp3_path_raw: str) -> str | None:
    mp3_path = Path(mp3_path_raw)
    separator = _get_worker_separator()

    base = mp3_path.stem
    # Some separators strip a trailing underscore from the source stem.
    # Mirror that behavior so expected outputs match actual filenames.
    base_normalized = base[:-1] if base.endswith("_") else base
    output_names = {
        "Instrumental": f"{base_normalized}{INSTRUMENTAL_SUFFIX}",
        "Vocals": f"{base_normalized}{VOCALS_SUFFIX}",
    }

    if hasattr(separator, "output_dir"):
        separator.output_dir = str(mp3_path.parent)
    if getattr(separator, "model_instance", None) is not None and hasattr(
        separator.model_instance, "output_dir"
    ):
        separator.model_instance.output_dir = str(mp3_path.parent)

    output_files = separator.separate(str(mp3_path), output_names)

    expected = [
        mp3_path.parent / f"{base_normalized}{INSTRUMENTAL_SUFFIX}.mp3",
        mp3_path.parent / f"{base_normalized}{VOCALS_SUFFIX}.mp3",
    ]
    if all(p.exists() for p in expected):
        mp3_path.unlink()
        return None

    # Keep original if separation failed or output names differed.
    return (
        "Warning: expected outputs not found for "
        f"{mp3_path}. Produced: {output_files}"
    )


def separate_mp3s(root: Path, jobs: int = DEFAULT_JOBS) -> None:
    files = collect_mp3_files(root)
    if not files:
        print(
            "No source .mp3 files found for separation "
            "(existing _(Vocals)/_(Instrumental) files will still be processed)."
        )
        return

    _prepare_model_cache(MODEL_DATA_DIR, MODEL_FILENAME)

    max_workers = min(jobs, len(files))
    if max_workers == 1:
        for mp3_path in tqdm(files, desc="Separating", unit="file"):
            try:
                warning = _separate_one_mp3(str(mp3_path))
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: separation failed for {mp3_path}: {exc}")
                continue
            if warning:
                print(warning)
        return

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_separate_one_mp3, str(mp3_path)): mp3_path
            for mp3_path in files
        }
        for future in tqdm(
            as_completed(futures), total=len(futures), desc="Separating", unit="file"
        ):
            mp3_path = futures[future]
            try:
                warning = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: separation failed for {mp3_path}: {exc}")
                continue
            if warning:
                print(warning)


def _get_worker_aligner() -> LyricsAligner:
    global _WORKER_ALIGNER
    if _WORKER_ALIGNER is None:
        _WORKER_ALIGNER = LyricsAligner()
    return _WORKER_ALIGNER


def _align_one_lyrics(lyrics_path_raw: str, force: bool = False) -> list[str]:
    lyrics_path = Path(lyrics_path_raw)
    warnings: list[str] = []

    vocals_path = _find_vocals_for_lyrics(lyrics_path)
    if vocals_path is None:
        return [f"Warning: vocals not found for {lyrics_path}"]

    output_path = _karaoke_output_path(vocals_path)
    if output_path.exists() and not force:
        try:
            if output_path.stat().st_mtime >= lyrics_path.stat().st_mtime:
                return warnings
        except OSError:
            # If stat fails, fall through and attempt to regenerate.
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

    aligner = _get_worker_aligner()
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


def process_genius_lyrics(
    root: Path, force: bool = False, jobs: int = DEFAULT_JOBS
) -> None:
    files = collect_genius_lyrics_files(root)
    if not files:
        print("No Genius lyrics files found.")
        return

    max_workers = min(jobs, len(files))
    if max_workers == 1:
        for lyrics_path in tqdm(files, desc="Aligning lyrics", unit="file"):
            try:
                warnings = _align_one_lyrics(str(lyrics_path), force=force)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: alignment failed for {lyrics_path}: {exc}")
                continue
            for warning in warnings:
                print(warning)
        return

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_align_one_lyrics, str(lyrics_path), force): lyrics_path
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively separate MP3 files into Instrumental/Vocals and "
            "align Genius lyrics into karaoke JSON with per-word timings. "
            "Order: separate MP3 -> fetch missing Genius lyrics -> alignment. "
            "Use --only-align to regenerate only karaoke JSON from existing "
            "_(Genius Lyrics).txt files."
        )
    )
    parser.add_argument(
        "path",
        type=Path,
        help="File or directory to process (recursively for directories).",
    )
    parser.add_argument(
        "--genius-delay-seconds",
        type=float,
        default=DEFAULT_GENIUS_DELAY_SECONDS,
        help=(
            "Delay between Genius requests in seconds "
            f"(default: {DEFAULT_GENIUS_DELAY_SECONDS})."
        ),
    )
    parser.add_argument(
        "--only-align",
        action="store_true",
        help=(
            "Skip separation and Genius fetching; only align existing "
            "_(Genius Lyrics).txt files into karaoke JSON."
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Number of worker processes for separation/alignment (default: {DEFAULT_JOBS}).",
    )
    args = parser.parse_args()
    if args.genius_delay_seconds < 0:
        parser.error("--genius-delay-seconds must be >= 0")
    if args.jobs < 1:
        parser.error("--jobs/-j must be >= 1")
    return args


def main() -> None:
    args = parse_args()
    if args.only_align:
        process_genius_lyrics(args.path, force=True, jobs=args.jobs)
        return
    separate_mp3s(args.path, jobs=args.jobs)
    root = _processing_root_after_separation(args.path)
    fetch_missing_genius_lyrics(root, args.genius_delay_seconds)
    process_genius_lyrics(root, jobs=args.jobs)


if __name__ == "__main__":
    main()
