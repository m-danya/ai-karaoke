from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Tuple

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
