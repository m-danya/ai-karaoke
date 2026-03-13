from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Tuple

import numpy as np

from .constants import DEFAULT_CH, DEFAULT_SR


def decode_mp3_to_float32(path: Path, sr: int = DEFAULT_SR, ch: int = DEFAULT_CH) -> np.ndarray:
    """
    Decode MP3 to float32 PCM in range ~[-1, 1], shape (num_frames, ch).

    Uses ffmpeg:
      ffmpeg -i input.mp3 -f f32le -ac 2 -ar 44100 pipe:1
    """
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-f",
        "f32le",
        "-ac",
        str(ch),
        "-ar",
        str(sr),
        "pipe:1",
    ]
    out = subprocess.check_output(cmd)
    data = np.frombuffer(out, dtype=np.float32)
    if data.size % ch != 0:
        data = data[: data.size - (data.size % ch)]
    return data.reshape(-1, ch)


def compute_vocals_env(
    vocals: np.ndarray, sr: int, hop_sec: float = 0.08
) -> Tuple[np.ndarray, int, float]:
    hop = max(1, int(sr * hop_sec))
    mono = vocals.mean(axis=1)
    n = mono.shape[0]
    pad = (-n) % hop
    if pad:
        mono = np.pad(mono, (0, pad))
    frames = mono.reshape(-1, hop)
    env = np.sqrt(np.mean(frames * frames, axis=1))
    if env.size >= 5:
        k = 5
        kernel = np.ones(k, dtype=np.float32) / k
        env = np.convolve(env, kernel, mode="same")
    env_max = float(np.max(env)) if env.size else 1.0
    if env_max <= 1e-6:
        env_max = 1.0
    return env, hop, env_max


def _atempo_values(target: float) -> List[float]:
    remaining = target
    values: List[float] = []
    while remaining < 0.5:
        values.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        values.append(2.0)
        remaining /= 2.0
    values.append(remaining)
    return values


def _pitch_shift_filter(semitones: int, sr: int) -> str:
    pitch_factor = 2.0 ** (semitones / 12.0)
    filters = [
        f"aresample={sr}",
        f"asetrate={sr}*{pitch_factor:.10f}",
        f"aresample={sr}",
    ]
    filters.extend(f"atempo={value:.10f}" for value in _atempo_values(1.0 / pitch_factor))
    return ",".join(filters)


def transpose_mp3(
    input_path: Path,
    output_path: Path,
    semitones: int,
    *,
    sr: int = DEFAULT_SR,
) -> None:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-af",
        _pitch_shift_filter(semitones, sr),
        "-ar",
        str(sr),
        "-q:a",
        "2",
        str(output_path),
    ]
    _run_ffmpeg_command(cmd)


def mix_stems_to_mp3(
    vocals_path: Path,
    instrumental_path: Path,
    output_path: Path,
    *,
    vocals_gain: float = 1.0,
    instr_gain: float = 1.0,
    vocals_muted: bool = False,
    instr_muted: bool = False,
    sr: int = DEFAULT_SR,
) -> None:
    effective_vocals_gain = 0.0 if vocals_muted else max(0.0, float(vocals_gain))
    effective_instr_gain = 0.0 if instr_muted else max(0.0, float(instr_gain))
    try:
        _run_ffmpeg_command(
            _mix_stems_command(
                vocals_path,
                instrumental_path,
                output_path,
                vocals_gain=effective_vocals_gain,
                instr_gain=effective_instr_gain,
                sr=sr,
                use_limiter=True,
            )
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").lower()
        if "alimiter" not in details or "no such filter" not in details:
            raise
        _run_ffmpeg_command(
            _mix_stems_command(
                vocals_path,
                instrumental_path,
                output_path,
                vocals_gain=effective_vocals_gain,
                instr_gain=effective_instr_gain,
                sr=sr,
                use_limiter=False,
            )
        )


def _run_ffmpeg_command(cmd: List[str]) -> None:
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _mix_stems_command(
    vocals_path: Path,
    instrumental_path: Path,
    output_path: Path,
    *,
    vocals_gain: float,
    instr_gain: float,
    sr: int,
    use_limiter: bool,
) -> List[str]:
    mix_tail = "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0"
    if use_limiter:
        mix_tail = f"{mix_tail},alimiter=limit=0.98"
    filter_complex = (
        f"[0:a]aresample={sr},volume={vocals_gain:.10f}[vocals];"
        f"[1:a]aresample={sr},volume={instr_gain:.10f}[instr];"
        f"[vocals][instr]{mix_tail}[out]"
    )
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(vocals_path),
        "-i",
        str(instrumental_path),
        "-vn",
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-ar",
        str(sr),
        "-ac",
        str(DEFAULT_CH),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "0",
        str(output_path),
    ]
