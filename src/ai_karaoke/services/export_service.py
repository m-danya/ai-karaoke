from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from ..audio import mix_stems_to_mp3
from ..models import ExportMixSettings, SongPair


def ffmpeg_error_details(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "ffmpeg is required in PATH."
    if isinstance(exc, subprocess.CalledProcessError):
        details = (exc.stderr or "").strip()
        if details:
            return details
    return str(exc)


def validate_export_destination(pair: SongPair, output_path: Path) -> None:
    try:
        resolved_target = output_path.resolve(strict=False)
    except OSError:
        resolved_target = output_path.absolute()
    try:
        source_paths = {
            pair.vocals.resolve(strict=False),
            pair.instrumental.resolve(strict=False),
        }
    except OSError:
        source_paths = {pair.vocals.absolute(), pair.instrumental.absolute()}
    if resolved_target in source_paths:
        raise ValueError(
            "Choose a new file name so the export does not overwrite one of the source stems."
        )


def render_mix_to_mp3(pair: SongPair, output_path: Path, mix_settings: ExportMixSettings, *, sr: int) -> None:
    temp_output: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f"{output_path.stem}.",
            suffix=".mp3",
            dir=str(output_path.parent),
            delete=False,
        ) as handle:
            temp_output = Path(handle.name)
        mix_stems_to_mp3(
            pair.vocals,
            pair.instrumental,
            temp_output,
            vocals_gain=mix_settings.vocals_gain,
            instr_gain=mix_settings.instr_gain,
            vocals_muted=mix_settings.vocals_muted,
            instr_muted=mix_settings.instr_muted,
            sr=sr,
        )
        temp_output.replace(output_path)
    except Exception:
        if temp_output is not None:
            try:
                temp_output.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        raise
