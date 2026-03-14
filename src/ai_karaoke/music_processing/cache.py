from __future__ import annotations

import os
from pathlib import Path
import sys
import zipfile

from audio_separator.separator import Separator


MODEL_FILENAME = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
MDX_PARAMS = {
    "hop_length": 1024,
    "segment_size": 256,
    "overlap": 0.2,
    "batch_size": 1,
    "enable_denoise": False,
}
_WARMED_SEPARATOR: Separator | None = None


def default_model_cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "ai-karaoke-models-cache"
        return Path.home() / "AppData" / "Local" / "ai-karaoke-models-cache"

    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home) / "ai-karaoke-models-cache"
    return Path.home() / ".cache" / "ai-karaoke-models-cache"


MODEL_DATA_DIR = default_model_cache_dir()


def is_valid_torch_checkpoint(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return bool(archive.namelist())
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        return False


def prepare_model_cache(model_dir: Path, model_filename: str = MODEL_FILENAME) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / model_filename
    if not model_path.exists():
        return
    if is_valid_torch_checkpoint(model_path):
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


def ensure_model_data_dir() -> Path:
    model_dir = MODEL_DATA_DIR
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def warmup_separator_model(model_dir: Path) -> None:
    global _WARMED_SEPARATOR
    separator = Separator(
        output_format="MP3",
        mdx_params=MDX_PARAMS,
        model_file_dir=str(model_dir),
    )
    separator.load_model(model_filename=MODEL_FILENAME)
    _WARMED_SEPARATOR = separator
