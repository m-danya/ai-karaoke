from __future__ import annotations

from tkinter import ttk


def scale_value_from_x(scale: ttk.Scale, x: int) -> float:
    width = max(1, scale.winfo_width())
    start = float(scale.cget("from"))
    end = float(scale.cget("to"))
    ratio = min(max(x / width, 0.0), 1.0)
    return start + (end - start) * ratio


def scale_step(scale: ttk.Scale, direction: int, step: float = 0.05) -> None:
    start = float(scale.cget("from"))
    end = float(scale.cget("to"))
    current = float(scale.get())
    next_value = min(max(current + direction * step, start), end)
    scale.set(next_value)


def wheel_direction(event) -> int:
    if getattr(event, "num", None) in (4, 5):
        return 1 if event.num == 4 else -1
    return 1 if getattr(event, "delta", 0) > 0 else -1
