from __future__ import annotations

from dataclasses import dataclass

from .constants import DEFAULT_CH, DEFAULT_SR
from .engine import TwoStemEngine


@dataclass
class MixState:
    vocals_gain: float = 1.0
    instr_gain: float = 1.0
    vocals_muted: bool = False
    instr_muted: bool = False
    vocals_last_unmuted: float = 1.0
    instr_last_unmuted: float = 1.0


class PlaybackController:
    def __init__(self, sr: int = DEFAULT_SR, ch: int = DEFAULT_CH, blocksize: int = 1024) -> None:
        self.engine = TwoStemEngine(sr=sr, ch=ch, blocksize=blocksize)
        self.mix = MixState()

    @property
    def sr(self) -> int:
        return self.engine.sr

    @property
    def ch(self) -> int:
        return self.engine.ch

    @property
    def playing(self) -> bool:
        return self.engine.playing

    def load(self, vocals, instrumental) -> None:
        self.engine.load(vocals, instrumental)

    def start(self) -> None:
        self.engine.start()

    def pause(self) -> None:
        self.engine.pause()

    def stop(self) -> None:
        self.engine.stop()

    def close(self) -> None:
        self.engine.close()

    def duration_seconds(self) -> float:
        return self.engine.duration_seconds()

    def position_seconds(self) -> float:
        return self.engine.position_seconds()

    def seek_seconds(self, t: float) -> None:
        self.engine.seek_seconds(t)

    def is_fading_out(self) -> bool:
        return self.engine.is_fading_out()

    def set_vocals_gain(self, value: float, smooth: bool = True) -> None:
        value = float(value)
        self.mix.vocals_gain = value
        self.mix.vocals_last_unmuted = value
        effective = 0.0 if self.mix.vocals_muted else value
        self.engine.set_vocals_gain(effective, smooth=smooth)

    def set_instr_gain(self, value: float, smooth: bool = True) -> None:
        value = float(value)
        self.mix.instr_gain = value
        self.mix.instr_last_unmuted = value
        effective = 0.0 if self.mix.instr_muted else value
        self.engine.set_instr_gain(effective, smooth=smooth)

    def set_vocals_muted(self, muted: bool, smooth: bool = True) -> None:
        muted = bool(muted)
        if muted == self.mix.vocals_muted:
            return
        if muted:
            self.mix.vocals_last_unmuted = self.mix.vocals_gain
            self.mix.vocals_muted = True
            self.engine.set_vocals_gain(0.0, smooth=smooth)
            return
        self.mix.vocals_muted = False
        if self.mix.vocals_gain == 0.0 and self.mix.vocals_last_unmuted != 0.0:
            self.mix.vocals_gain = self.mix.vocals_last_unmuted
        self.engine.set_vocals_gain(self.mix.vocals_gain, smooth=smooth)

    def set_instr_muted(self, muted: bool, smooth: bool = True) -> None:
        muted = bool(muted)
        if muted == self.mix.instr_muted:
            return
        if muted:
            self.mix.instr_last_unmuted = self.mix.instr_gain
            self.mix.instr_muted = True
            self.engine.set_instr_gain(0.0, smooth=smooth)
            return
        self.mix.instr_muted = False
        if self.mix.instr_gain == 0.0 and self.mix.instr_last_unmuted != 0.0:
            self.mix.instr_gain = self.mix.instr_last_unmuted
        self.engine.set_instr_gain(self.mix.instr_gain, smooth=smooth)

    def toggle_vocals_mute(self) -> None:
        self.set_vocals_muted(not self.mix.vocals_muted, smooth=True)

    def toggle_instr_mute(self) -> None:
        self.set_instr_muted(not self.mix.instr_muted, smooth=True)

    def set_vocals_full(self) -> None:
        self.set_vocals_gain(1.0, smooth=True)
        self.set_vocals_muted(False, smooth=True)

    def set_instr_full(self) -> None:
        self.set_instr_gain(1.0, smooth=True)
        self.set_instr_muted(False, smooth=True)
