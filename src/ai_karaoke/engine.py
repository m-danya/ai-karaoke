from __future__ import annotations

import threading
from typing import Optional, Tuple

import numpy as np
import sounddevice as sd

from .constants import DEFAULT_CH, DEFAULT_SR


class TwoStemEngine:
    def __init__(self, sr: int = DEFAULT_SR, ch: int = DEFAULT_CH, blocksize: int = 1024) -> None:
        self.sr = sr
        self.ch = ch
        self.blocksize = blocksize

        self._lock = threading.Lock()
        self._vocals: Optional[np.ndarray] = None
        self._instr: Optional[np.ndarray] = None
        self._pos = 0  # in frames

        self.vocals_gain = 1.0
        self.instr_gain = 1.0
        self._v_gain_current = 1.0
        self._i_gain_current = 1.0
        self._v_gain_target = 1.0
        self._i_gain_target = 1.0
        self._v_gain_remaining = 0
        self._i_gain_remaining = 0
        self.playing = False

        self._fade_total_frames = int(0.5 * self.sr)
        self._fade_out_remaining = 0
        self._fade_in_remaining = 0
        self._gain_fade_total_frames = self._fade_total_frames

        self._stream: Optional[sd.OutputStream] = None

    def load(self, vocals: np.ndarray, instrumental: np.ndarray) -> None:
        with self._lock:
            # Pad to same length
            n = max(vocals.shape[0], instrumental.shape[0])
            self._vocals = self._pad(vocals, n)
            self._instr = self._pad(instrumental, n)
            self._pos = 0

    def _pad(self, x: np.ndarray, n: int) -> np.ndarray:
        if x.shape[0] >= n:
            return x
        pad = np.zeros((n - x.shape[0], self.ch), dtype=np.float32)
        return np.vstack([x, pad])

    def duration_seconds(self) -> float:
        with self._lock:
            if self._vocals is None:
                return 0.0
            return self._vocals.shape[0] / self.sr

    def position_seconds(self) -> float:
        with self._lock:
            return self._pos / self.sr

    def seek_seconds(self, t: float) -> None:
        with self._lock:
            if self._vocals is None:
                return
            duration = self._vocals.shape[0] / self.sr
            t = max(0.0, min(t, duration))
            self._pos = int(t * self.sr)

    def start(self) -> None:
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=self.sr,
                channels=self.ch,
                dtype="float32",
                blocksize=self.blocksize,
                callback=self._callback,
            )
            self._stream.start()
        self.playing = True
        self._fade_out_remaining = 0
        self._fade_in_remaining = max(1, self._fade_total_frames)

    def pause(self) -> None:
        if not self.playing:
            return
        self._fade_out_remaining = max(1, self._fade_total_frames)
        self._fade_in_remaining = 0

    def stop(self) -> None:
        self.playing = False
        self._fade_out_remaining = 0
        self._fade_in_remaining = 0
        with self._lock:
            self._pos = 0

    def close(self) -> None:
        self.playing = False
        self._fade_out_remaining = 0
        self._fade_in_remaining = 0
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def set_vocals_gain(self, target: float, smooth: bool = True) -> None:
        target = float(target)
        with self._lock:
            self.vocals_gain = target
            self._v_gain_target = target
            if not smooth:
                self._v_gain_current = target
                self._v_gain_remaining = 0
            else:
                self._v_gain_remaining = max(1, self._gain_fade_total_frames)

    def set_instr_gain(self, target: float, smooth: bool = True) -> None:
        target = float(target)
        with self._lock:
            self.instr_gain = target
            self._i_gain_target = target
            if not smooth:
                self._i_gain_current = target
                self._i_gain_remaining = 0
            else:
                self._i_gain_remaining = max(1, self._gain_fade_total_frames)

    def _apply_gain_ramp(
        self, chunk: np.ndarray, current: float, target: float, remaining: int
    ) -> Tuple[np.ndarray, float, int]:
        n = chunk.shape[0]
        if n == 0:
            return chunk, current, remaining
        if remaining <= 0 or current == target:
            return chunk * current, current, 0

        step = min(n, remaining)
        if step <= 0:
            return chunk * current, current, remaining

        g1 = target if step == remaining else current + (target - current) * (step / remaining)
        if step < n:
            gains = np.empty(n, dtype=np.float32)
            gains[:step] = np.linspace(current, g1, num=step, endpoint=True, dtype=np.float32)
            gains[step:] = g1
        else:
            gains = np.linspace(current, g1, num=n, endpoint=True, dtype=np.float32)

        return chunk * gains[:, None], g1, max(0, remaining - step)

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        outdata.fill(0.0)
        if not self.playing and self._fade_out_remaining <= 0:
            return

        with self._lock:
            v = self._vocals
            i = self._instr
            pos = self._pos
            fade_out_remaining = self._fade_out_remaining
            fade_in_remaining = self._fade_in_remaining
            fade_total = self._fade_total_frames
            v_current = self._v_gain_current
            i_current = self._i_gain_current
            v_target = self._v_gain_target
            i_target = self._i_gain_target
            v_remaining = self._v_gain_remaining
            i_remaining = self._i_gain_remaining

            if v is None or i is None:
                return

            end = min(pos + frames, v.shape[0])
            chunk_v = v[pos:end]
            chunk_i = i[pos:end]

            v_scaled, v_current, v_remaining = self._apply_gain_ramp(
                chunk_v, v_current, v_target, v_remaining
            )
            i_scaled, i_current, i_remaining = self._apply_gain_ramp(
                chunk_i, i_current, i_target, i_remaining
            )
            mixed = v_scaled + i_scaled
            if fade_total > 0:
                gain = 1.0
                if fade_out_remaining > 0:
                    gain = min(gain, fade_out_remaining / fade_total)
                if fade_in_remaining > 0:
                    gain = min(gain, 1.0 - (fade_in_remaining / fade_total))
                if gain < 1.0:
                    mixed *= gain
            # Soft clip to [-1, 1] to avoid harsh distortion
            mixed = np.tanh(mixed)

            outdata[: mixed.shape[0], :] = mixed
            if mixed.shape[0] < frames:
                # Reached end
                self.playing = False
                self._fade_in_remaining = 0

            self._pos = end
            self._v_gain_current = v_current
            self._i_gain_current = i_current
            self._v_gain_remaining = v_remaining
            self._i_gain_remaining = i_remaining
            if fade_out_remaining > 0:
                self._fade_out_remaining = max(0, fade_out_remaining - frames)
                if self._fade_out_remaining == 0:
                    self.playing = False
            if fade_in_remaining > 0:
                self._fade_in_remaining = max(0, fade_in_remaining - frames)

    def is_fading_out(self) -> bool:
        return self._fade_out_remaining > 0
