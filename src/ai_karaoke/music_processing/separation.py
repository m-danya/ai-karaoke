from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
from pathlib import Path

from audio_separator.separator import Separator
from tqdm import tqdm

from .cache import MDX_PARAMS, MODEL_DATA_DIR, MODEL_FILENAME, ensure_model_data_dir, prepare_model_cache, warmup_separator_model
from .common import DEFAULT_JOBS
from .io_paths import INSTRUMENTAL_SUFFIX, collect_mp3_files
from .genius_fetch import VOCALS_SUFFIX


_WORKER_SEPARATOR: Separator | None = None
_SPAWN_CTX = mp.get_context("spawn")


def get_worker_separator() -> Separator:
    global _WORKER_SEPARATOR
    if _WORKER_SEPARATOR is None:
        _WORKER_SEPARATOR = Separator(
            output_format="MP3",
            mdx_params=MDX_PARAMS,
            model_file_dir=str(MODEL_DATA_DIR),
        )
        _WORKER_SEPARATOR.load_model(model_filename=MODEL_FILENAME)
    return _WORKER_SEPARATOR


def separate_one_mp3(mp3_path_raw: str) -> str | None:
    mp3_path = Path(mp3_path_raw)
    separator = get_worker_separator()

    base = mp3_path.stem
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
    if all(path.exists() for path in expected):
        mp3_path.unlink()
        return None
    return f"Warning: expected outputs not found for {mp3_path}. Produced: {output_files}"


def separate_mp3s(root: Path, jobs: int = DEFAULT_JOBS) -> None:
    files = collect_mp3_files(root)
    if not files:
        print(
            "No source .mp3 files found for separation "
            "(existing _(Vocals)/_(Instrumental) files will still be processed)."
        )
        return

    model_dir = ensure_model_data_dir()
    prepare_model_cache(model_dir, MODEL_FILENAME)
    warmup_separator_model(model_dir)

    max_workers = min(jobs, len(files))
    if max_workers == 1:
        for mp3_path in tqdm(files, desc="Separating", unit="file"):
            try:
                warning = separate_one_mp3(str(mp3_path))
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: separation failed for {mp3_path}: {exc}")
                continue
            if warning:
                print(warning)
        return

    with ProcessPoolExecutor(max_workers=max_workers, mp_context=_SPAWN_CTX) as executor:
        futures = {executor.submit(separate_one_mp3, str(mp3_path)): mp3_path for mp3_path in files}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Separating", unit="file"):
            mp3_path = futures[future]
            try:
                warning = future.result()
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: separation failed for {mp3_path}: {exc}")
                continue
            if warning:
                print(warning)
