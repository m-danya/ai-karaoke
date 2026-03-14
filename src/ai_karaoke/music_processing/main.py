#!/usr/bin/env python3
from __future__ import annotations

from .alignment_pipeline import align_one_lyrics as _align_one_lyrics
from .alignment_pipeline import get_worker_aligner as _get_worker_aligner
from .alignment_pipeline import process_genius_lyrics
from .cache import MODEL_DATA_DIR, MODEL_FILENAME, default_model_cache_dir as _default_model_cache_dir
from .cache import ensure_model_data_dir as _ensure_model_data_dir
from .cache import is_valid_torch_checkpoint as _is_valid_torch_checkpoint
from .cache import prepare_model_cache as _prepare_model_cache
from .cache import warmup_separator_model as _warmup_separator_model
from .cli import parse_args
from .common import DEFAULT_JOBS
from .io_paths import (
    INSTRUMENTAL_SUFFIX,
    KARAOKE_LYRICS_SUFFIX,
    RESULT_SUFFIXES,
    collect_genius_lyrics_files,
    collect_mp3_files,
    find_vocals_for_lyrics as _find_vocals_for_lyrics,
    is_genius_lyrics_txt as _is_genius_lyrics_txt,
    is_result_mp3 as _is_result_mp3,
    karaoke_output_path as _karaoke_output_path,
    processing_root_after_separation as _processing_root_after_separation,
)
from .pipeline import run_align_only, run_pipeline
from .separation import get_worker_separator as _get_worker_separator
from .separation import separate_mp3s, separate_one_mp3 as _separate_one_mp3


def main() -> None:
    args = parse_args()
    if args.only_align:
        run_align_only(args.path, jobs=args.jobs)
        return
    run_pipeline(
        args.path,
        genius_delay_seconds=args.genius_delay_seconds,
        jobs=args.jobs,
    )


if __name__ == "__main__":
    main()
