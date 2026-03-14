from __future__ import annotations

import argparse
from pathlib import Path

from .common import DEFAULT_JOBS
from .genius_fetch import DEFAULT_GENIUS_DELAY_SECONDS


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
