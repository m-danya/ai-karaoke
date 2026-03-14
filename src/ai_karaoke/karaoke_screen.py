from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence

import tkinter as tk
from tkinter import font as tkfont, ttk


@dataclass(frozen=True)
class KaraokeCallbacks:
    on_exit_request: Callable[[], None]
    on_play_toggle: Callable[[], None]
    on_seek: Callable[[float], None]
    on_loop_in: Callable[[], None]
    on_loop_out: Callable[[], None]
    on_loop_clear: Callable[[], None]
    on_v_gain: Callable[[float], None]
    on_i_gain: Callable[[float], None]
    on_v_mute: Callable[[], None]
    on_i_mute: Callable[[], None]
    on_v_full: Callable[[], None]
    on_i_full: Callable[[], None]
    on_record_next: Callable[[], None]
    on_record_break: Callable[[], None]
    on_font_smaller: Callable[[], None]
    on_font_larger: Callable[[], None]
    on_lines_fewer: Callable[[], None]
    on_lines_more: Callable[[], None]
    on_toggle_countdown: Callable[[], None]
    on_toggle_finish_celebration: Callable[[], None]


@dataclass
class _CanvasWordItem:
    base_id: int
    full_id: int
    part_id: int
    token: str


class KaraokeScreen:
    _MIN_VISIBLE_LINES = 1
    _MAX_VISIBLE_LINES = 7

    def __init__(
        self,
        parent: tk.Tk,
        colors: Dict[str, str],
        lyrics_font_size: int,
        visible_lines: int,
        countdown_enabled: bool,
        finish_celebration_enabled: bool,
        callbacks: KaraokeCallbacks,
    ) -> None:
        self.parent = parent
        self.colors = colors
        self.cb = callbacks

        self.window: Optional[tk.Toplevel] = None
        self.mode: Optional[str] = None
        self._ready = False
        self._ignore_seek_events = False
        self._ignore_gain_events = False
        self._seeking = False
        self._clear_seek_job: Optional[str] = None
        self._play_controls_enabled = True
        self._loop_has_start = False
        self._loop_has_range = False
        self._loop_active = False

        self._panel_width = 1100
        self._record_pack = {"pady": (14, 0)}
        self._button_width = 12
        self._button_wide = 22
        self.scope_panel: Optional[ttk.Frame] = None
        self.k_scope: Optional[tk.Canvas] = None
        self.mix_row: Optional[tk.Frame] = None
        self.record_panel: Optional[ttk.Frame] = None
        self.tools_panel: Optional[ttk.Frame] = None
        self.k_recording_status: Optional[ttk.Label] = None
        self.k_btn_record_next: Optional[ttk.Button] = None
        self.k_btn_record_break: Optional[ttk.Button] = None
        self.k_btn_font_smaller: Optional[ttk.Button] = None
        self.k_btn_font_larger: Optional[ttk.Button] = None
        self.k_btn_lines_fewer: Optional[ttk.Button] = None
        self.k_btn_lines_more: Optional[ttk.Button] = None
        self.k_btn_countdown_toggle: Optional[ttk.Button] = None
        self.k_btn_finish_toggle: Optional[ttk.Button] = None
        self.k_btn_loop_in: Optional[ttk.Button] = None
        self.k_btn_loop_out: Optional[ttk.Button] = None
        self.k_btn_loop_clear: Optional[ttk.Button] = None
        self.k_loop_status: Optional[tk.Label] = None
        self.k_song_title: Optional[tk.Label] = None
        self._song_title_text = ""
        self._lines_container: Optional[tk.Frame] = None
        self._record_parent: Optional[tk.Frame] = None
        self._record_row: int = 0
        self.k_current_line: Optional[tk.Frame] = None
        self._line_slots: list[tk.Frame] = []
        self._line_canvases: list[tk.Canvas] = []
        self._active_slot_idx = 0
        self._visible_line_count = self._clamp_visible_lines(visible_lines)
        self._countdown_enabled = bool(countdown_enabled)
        self._finish_celebration_enabled = bool(finish_celebration_enabled)
        self._lyrics_font_size = max(20, min(72, int(lyrics_font_size)))
        self._lyrics_font = tkfont.Font(
            root=parent,
            family="Playfair Display",
            size=self._lyrics_font_size,
            weight="bold",
        )
        self._lyrics_line_height = 1
        self._lyrics_line_gap = 6
        self._lyrics_display_gap = 2
        self._lyrics_space_width = 1
        self._recompute_lyrics_metrics()
        self._line_pad_x = 24
        self._current_line_width_px = 900
        self._current_line_height_px = max(
            96,
            self._lyrics_line_height * 2 + self._lyrics_line_gap,
        )
        self._slot_tokens: list[list[str]] = []
        self._slot_base_colors: list[str] = []
        self._slot_layout_w: list[int] = []
        self._slot_layout_h: list[int] = []
        self._slot_word_items: list[list[_CanvasWordItem]] = []
        self._display_slot_lines: tuple[str, ...] = tuple("" for _ in range(self._visible_line_count))
        self._display_active_slot = 0

    def _clamp_visible_lines(self, count: int) -> int:
        return max(self._MIN_VISIBLE_LINES, min(self._MAX_VISIBLE_LINES, int(count)))

    def _recompute_lyrics_metrics(self) -> None:
        self._lyrics_line_height = max(1, int(self._lyrics_font.metrics("linespace")))
        self._lyrics_line_gap = max(6, int(self._lyrics_line_height * 0.26))
        self._lyrics_display_gap = max(2, int(self._lyrics_line_height * 0.09))
        self._lyrics_space_width = max(1, int(self._lyrics_font.measure(" ")))

    def set_lyrics_font_size(self, size: int) -> int:
        clamped = max(20, min(72, int(size)))
        if clamped == self._lyrics_font_size:
            return clamped
        self._lyrics_font_size = clamped
        self._lyrics_font.configure(size=clamped)
        self._recompute_lyrics_metrics()
        self._invalidate_canvas_layouts()
        if self.window is not None and self.is_open():
            try:
                self._apply_resize(self.window.winfo_width(), self.window.winfo_height())
            except tk.TclError:
                pass
        return clamped

    def set_visible_line_count(self, count: int) -> int:
        clamped = self._clamp_visible_lines(count)
        if clamped == self._visible_line_count:
            return clamped
        self._visible_line_count = clamped
        self._display_slot_lines = tuple("" for _ in range(self._visible_line_count))
        self._display_active_slot = 0
        if self.window is not None and self.is_open():
            self._rebuild_line_slots()
            try:
                self._apply_resize(self.window.winfo_width(), self.window.winfo_height())
            except tk.TclError:
                pass
        return clamped

    def set_countdown_enabled(self, enabled: bool) -> None:
        self._countdown_enabled = bool(enabled)
        self._update_toggle_button_texts()

    def set_finish_celebration_enabled(self, enabled: bool) -> None:
        self._finish_celebration_enabled = bool(enabled)
        self._update_toggle_button_texts()

    def update_loop_state(
        self,
        loop_in: float | None,
        loop_out: float | None,
        enabled: bool,
        status_text: str,
    ) -> None:
        self._loop_has_start = loop_in is not None
        self._loop_has_range = loop_out is not None
        self._loop_active = bool(enabled and loop_in is not None and loop_out is not None)
        if not self._ready or not self.is_open():
            return
        self._apply_loop_controls_state()
        if self.k_loop_status is not None:
            color = self.colors["karaoke"] if self._loop_active else self.colors["muted"]
            self.k_loop_status.configure(text=status_text, fg=color)

    def _update_toggle_button_texts(self) -> None:
        countdown_state = "ON" if self._countdown_enabled else "OFF"
        finish_state = "ON" if self._finish_celebration_enabled else "OFF"
        if self.k_btn_countdown_toggle is not None:
            self.k_btn_countdown_toggle.configure(text=f"3..2..1 {countdown_state}")
        if self.k_btn_finish_toggle is not None:
            self.k_btn_finish_toggle.configure(text=f"Finish {finish_state}")

    def is_seeking(self) -> bool:
        return self._seeking

    def is_open(self) -> bool:
        return self.window is not None and self.window.winfo_exists()

    def scope_canvas(self) -> Optional[tk.Canvas]:
        if not self._ready or not self.is_open():
            return None
        return self.k_scope

    def open(self, mode: str) -> None:
        if self.is_open():
            self.set_mode(mode)
            self._show_window()
            return

        self._ready = False
        self._seeking = False
        self._clear_seek_job = None
        self.mode = mode
        self.window = tk.Toplevel(self.parent)
        win = self.window
        win.title("AI Karaoke")
        win.configure(bg=self.colors["bg"])
        win.attributes("-fullscreen", True)
        win.bind("<Escape>", lambda event: self.cb.on_exit_request())
        win.protocol("WM_DELETE_WINDOW", self.cb.on_exit_request)
        try:
            w = win.winfo_screenwidth()
            h = win.winfo_screenheight()
            win.geometry(f"{w}x{h}+0+0")
        except tk.TclError:
            w = win.winfo_width()
            h = win.winfo_height()

        root = tk.Frame(win, bg=self.colors["bg"])
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_header(root)
        self._build_center(root)
        self._build_bottom(root)

        self._ready = True
        if mode == "record":
            self._ensure_record_panel()
            self._set_record_panel_visible(True)
        else:
            self._set_record_panel_visible(False)
        self._apply_resize(w, h)
        self._show_window()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "record":
            self._ensure_record_panel()
            self._set_record_panel_visible(True)
            if self.window is not None:
                try:
                    self._apply_resize(self.window.winfo_width(), self.window.winfo_height())
                except tk.TclError:
                    pass
        else:
            self._set_record_panel_visible(False)
        self._apply_loop_controls_state()

    def close(self) -> None:
        if self.window is None:
            return
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.window = None
        self.mode = None
        self._ready = False
        self._seeking = False
        self._clear_seek_job = None
        self._play_controls_enabled = True
        self._loop_has_start = False
        self._loop_has_range = False
        self._loop_active = False
        self.scope_panel = None
        self.k_scope = None
        self.mix_row = None
        self.record_panel = None
        self.tools_panel = None
        self.k_recording_status = None
        self.k_btn_record_next = None
        self.k_btn_record_break = None
        self.k_btn_font_smaller = None
        self.k_btn_font_larger = None
        self.k_btn_lines_fewer = None
        self.k_btn_lines_more = None
        self.k_btn_countdown_toggle = None
        self.k_btn_finish_toggle = None
        self.k_btn_loop_in = None
        self.k_btn_loop_out = None
        self.k_btn_loop_clear = None
        self.k_loop_status = None
        self.k_song_title = None
        self._song_title_text = ""
        self._lines_container = None
        self._record_parent = None
        self._record_row = 0
        self.k_current_line = None
        self._line_slots = []
        self._line_canvases = []
        self._active_slot_idx = 0
        self._slot_tokens = []
        self._slot_base_colors = []
        self._slot_layout_w = []
        self._slot_layout_h = []
        self._slot_word_items = []
        self._display_slot_lines = tuple("" for _ in range(self._visible_line_count))
        self._display_active_slot = 0

    def _apply_loop_controls_state(self) -> None:
        base_state = "normal" if self._play_controls_enabled and self.mode != "record" else "disabled"
        if self.k_btn_loop_in is not None:
            self.k_btn_loop_in.configure(state=base_state)
        if self.k_btn_loop_out is not None:
            self.k_btn_loop_out.configure(
                state=base_state if base_state == "normal" and self._loop_has_start else "disabled"
            )
        if self.k_btn_loop_clear is not None:
            self.k_btn_loop_clear.configure(
                state=base_state if base_state == "normal" and self._loop_has_start else "disabled"
            )

    def _normalize_slot_lines(self, slot_lines: Sequence[str]) -> tuple[str, ...]:
        count = self._visible_line_count
        normalized = [str(slot_lines[idx]) if idx < len(slot_lines) else "" for idx in range(count)]
        return tuple(normalized)

    def update_playback(self, pos: float, dur: float, playing: bool, seeking: bool) -> None:
        if not self._ready or not self.is_open():
            return
        if float(self.k_seek.cget("to")) != dur:
            self.k_seek.configure(to=dur)
        self.k_time_lbl.configure(text=f"{self._format_time(pos)} / {self._format_time(dur)}")
        if not seeking:
            self._ignore_seek_events = True
            self.k_seek.set(pos)
            self._ignore_seek_events = False
        self.k_btn_play.configure(text="Pause" if playing else "Play")

    def update_lines(
        self,
        slot_lines: Sequence[str],
        active_slot: int,
    ) -> None:
        if not self._ready or not self.is_open():
            return
        normalized_lines = self._normalize_slot_lines(slot_lines)
        self._sync_slot_display(normalized_lines, active_slot)
        self._render_plain_slots(normalized_lines)

    def update_lines_with_words(
        self,
        slot_lines: Sequence[str],
        active_slot: int,
        words: list[str],
        sung_words: int,
        active_word_idx: int | None,
        active_word_progress: float,
    ) -> None:
        if not self._ready or not self.is_open():
            return
        normalized_lines = self._normalize_slot_lines(slot_lines)
        self._sync_slot_display(normalized_lines, active_slot)
        self._render_word_slots(
            normalized_lines,
            words,
            sung_words,
            active_word_idx,
            active_word_progress,
        )

    def _sync_slot_display(self, slot_lines: tuple[str, ...], active_slot: int) -> bool:
        normalized_slot = self._normalize_slot_index(active_slot)
        changed = (
            slot_lines != self._display_slot_lines
            or normalized_slot != self._display_active_slot
        )
        if not changed:
            return False
        self._set_active_slot(normalized_slot)
        self._display_slot_lines = slot_lines
        self._display_active_slot = normalized_slot
        return True

    def _normalize_slot_index(self, slot_idx: int) -> int:
        if not self._line_slots:
            return 0
        return int(slot_idx) % len(self._line_slots)

    def _set_active_slot(self, active_slot: int) -> None:
        if not self._line_slots:
            self.k_current_line = None
            self._active_slot_idx = 0
            return
        next_slot = self._normalize_slot_index(active_slot)
        self._active_slot_idx = next_slot
        self.k_current_line = self._line_slots[next_slot]

    def update_volume(self, vocals: float, instr: float, v_muted: bool, i_muted: bool) -> None:
        if not self._ready or not self.is_open():
            return
        self._ignore_gain_events = True
        self.k_v_slider.set(vocals)
        self.k_i_slider.set(instr)
        self._ignore_gain_events = False
        self.k_v_slider.configure(state="disabled" if v_muted else "normal")
        self.k_i_slider.configure(state="disabled" if i_muted else "normal")
        self.k_mute_v.configure(style="MuteActive.TButton" if v_muted else "Mute.TButton")
        self.k_mute_i.configure(style="MuteActive.TButton" if i_muted else "Mute.TButton")

    def update_recording_status(self, text: str) -> None:
        if not self._ready or not self.is_open():
            return
        if self.mode == "record":
            self._ensure_record_panel()
        if self.k_recording_status is None:
            return
        self.k_recording_status.configure(text=text)

    def set_record_controls_enabled(self, enabled: bool) -> None:
        if not self._ready or not self.is_open():
            return
        if self.mode != "record":
            return
        self._ensure_record_panel()
        if self.k_btn_record_next is None or self.k_btn_record_break is None:
            return
        state = "normal" if enabled else "disabled"
        self.k_btn_record_next.configure(state=state)
        self.k_btn_record_break.configure(state=state)
        self._set_record_panel_visible(True)

    def set_play_controls_enabled(self, enabled: bool) -> None:
        if not self._ready or not self.is_open():
            return
        self._play_controls_enabled = bool(enabled)
        state = "normal" if enabled else "disabled"
        self.k_btn_play.configure(state=state)
        self.k_seek.configure(state=state)
        if self.k_btn_countdown_toggle is not None:
            self.k_btn_countdown_toggle.configure(state=state)
        if self.k_btn_finish_toggle is not None:
            self.k_btn_finish_toggle.configure(state=state)
        self._apply_loop_controls_state()

    def set_song_title(self, title: str) -> None:
        normalized = str(title).strip()
        if normalized == self._song_title_text:
            return
        self._song_title_text = normalized
        if not self._ready or not self.is_open() or self.k_song_title is None:
            return
        self.k_song_title.configure(text=normalized)

    def _build_header(self, parent: tk.Frame) -> None:
        top = tk.Frame(parent, bg=self.colors["bg"])
        top.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 10))
        top.columnconfigure(1, weight=1)
        self.k_exit = ttk.Button(
            top, text="Back", command=self.cb.on_exit_request, width=self._button_width
        )
        self.k_exit.grid(row=0, column=0, sticky="w")

    def _build_center(self, parent: tk.Frame) -> None:
        center = tk.Frame(parent, bg=self.colors["bg"])
        center.grid(row=1, column=0, sticky="nsew", padx=60, pady=10)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=0)
        center.rowconfigure(1, weight=1)
        center.rowconfigure(3, weight=1)
        self.k_song_title = tk.Label(
            center,
            text=self._song_title_text,
            font=("Fira Sans", 18, "bold"),
            bg=self.colors["bg"],
            fg=self.colors["text"],
            justify="center",
            anchor="center",
        )
        self.k_song_title.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        tk.Frame(center, bg=self.colors["bg"]).grid(row=1, column=0, sticky="nsew")
        self._lines_container = tk.Frame(center, bg=self.colors["bg"])
        self._lines_container.grid(row=2, column=0, sticky="")
        tk.Frame(center, bg=self.colors["bg"]).grid(row=3, column=0, sticky="nsew")

        self._rebuild_line_slots()

    def _rebuild_line_slots(self) -> None:
        if self._lines_container is None:
            return
        for child in self._lines_container.winfo_children():
            child.destroy()
        self._line_slots = []
        self._line_canvases = []
        self._slot_tokens = []
        self._slot_base_colors = []
        self._slot_layout_w = []
        self._slot_layout_h = []
        self._slot_word_items = []
        count = self._visible_line_count
        for slot_idx in range(count):
            slot = tk.Frame(
                self._lines_container,
                width=self._current_line_width_px,
                height=self._current_line_height_px,
                bg=self.colors["bg"],
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                takefocus=0,
                cursor="arrow",
            )
            slot.pack(pady=(0, self._lyrics_display_gap if slot_idx < count - 1 else 0))
            slot.pack_propagate(False)

            canvas = tk.Canvas(
                slot,
                bg=self.colors["bg"],
                highlightthickness=0,
                borderwidth=0,
                relief="flat",
                takefocus=0,
                cursor="arrow",
            )
            canvas.place(relx=0.5, rely=0.5, anchor="center", width=self._current_line_width_px, height=self._current_line_height_px)
            self._line_slots.append(slot)
            self._line_canvases.append(canvas)
            self._slot_tokens.append([])
            self._slot_base_colors.append("")
            self._slot_layout_w.append(0)
            self._slot_layout_h.append(0)
            self._slot_word_items.append([])

        self._active_slot_idx = 0
        self._set_active_slot(0)
        self._display_slot_lines = tuple("" for _ in range(self._visible_line_count))
        self._display_active_slot = 0

    def _build_bottom(self, parent: tk.Frame) -> None:
        bottom = tk.Frame(parent, bg=self.colors["bg"])
        bottom.grid(row=2, column=0, sticky="ew", padx=28, pady=(8, 22))

        self.bottom_stack = tk.Frame(bottom, bg=self.colors["bg"])
        self.bottom_stack.pack(anchor="center")
        self.bottom_stack.columnconfigure(0, weight=1)

        self._build_controls(self.bottom_stack, row=0)
        self.mix_row = tk.Frame(self.bottom_stack, bg=self.colors["bg"])
        self.mix_row.grid(row=1, column=0, pady=(14, 0))
        self.mix_row.columnconfigure(0, weight=1)
        self._build_mix(self.mix_row, row=0)
        self._build_tools(self.mix_row, row=0)
        self._build_scope(self.bottom_stack, row=2)
        self._record_parent = self.bottom_stack
        self._record_row = 3

    def _build_controls(self, parent: tk.Frame, row: int) -> None:
        self.controls_panel = ttk.Frame(parent, style="Panel.TFrame", width=self._panel_width)
        self.controls_panel.grid(row=row, column=0, pady=0)
        self.controls_panel.pack_propagate(False)
        self.controls_panel.columnconfigure(2, weight=1)

        self.k_btn_play = ttk.Button(
            self.controls_panel,
            text="Play",
            style="Accent.TButton",
            command=self.cb.on_play_toggle,
            width=self._button_width,
        )
        self.k_btn_play.grid(row=0, column=0, padx=(0, 12), pady=10)

        self.k_time_lbl = ttk.Label(self.controls_panel, text="00:00 / 00:00", style="Subtle.TLabel")
        self.k_time_lbl.grid(row=0, column=1, sticky="w", pady=10)

        self.k_seek = ttk.Scale(
            self.controls_panel,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            style="Seek.Horizontal.TScale",
            command=self._on_seek_drag,
        )
        self.k_seek.grid(row=0, column=2, sticky="ew", padx=(12, 0), pady=10)
        self.k_seek.bind("<Button-1>", self._on_seek_click)
        self.k_seek.bind("<B1-Motion>", self._on_seek_motion)

    def _build_mix(self, parent: tk.Frame, row: int) -> None:
        self.mix_panel = ttk.Frame(parent, style="Panel.TFrame", width=self._panel_width)
        self.mix_panel.grid(row=row, column=0)
        self.mix_panel.pack_propagate(False)
        self.mix_panel.columnconfigure(1, weight=1)

        ttk.Label(self.mix_panel, text="Vocals", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
        self.k_v_slider = ttk.Scale(
            self.mix_panel,
            from_=0.0,
            to=1.5,
            orient="horizontal",
            style="Volume.Horizontal.TScale",
            command=self._on_v_gain,
        )
        self.k_v_slider.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        self.k_v_slider.bind("<Button-1>", self._on_v_click)
        self.k_v_slider.bind("<B1-Motion>", self._on_v_motion)
        self.k_v_slider.bind("<MouseWheel>", self._on_v_wheel)
        self.k_v_slider.bind("<Button-4>", self._on_v_wheel)
        self.k_v_slider.bind("<Button-5>", self._on_v_wheel)
        self.k_mute_v = ttk.Button(
            self.mix_panel,
            text="Mute",
            style="Mute.TButton",
            command=self.cb.on_v_mute,
            width=self._button_width,
        )
        self.k_mute_v.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self.k_full_v = ttk.Button(
            self.mix_panel, text="100%", command=self.cb.on_v_full, width=self._button_width
        )
        self.k_full_v.grid(row=0, column=3, sticky="ew")

        ttk.Label(self.mix_panel, text="Instrumental", style="Subtle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        self.k_i_slider = ttk.Scale(
            self.mix_panel,
            from_=0.0,
            to=1.5,
            orient="horizontal",
            style="Volume.Horizontal.TScale",
            command=self._on_i_gain,
        )
        self.k_i_slider.grid(row=1, column=1, sticky="ew", padx=(10, 8), pady=(8, 0))
        self.k_i_slider.bind("<Button-1>", self._on_i_click)
        self.k_i_slider.bind("<B1-Motion>", self._on_i_motion)
        self.k_i_slider.bind("<MouseWheel>", self._on_i_wheel)
        self.k_i_slider.bind("<Button-4>", self._on_i_wheel)
        self.k_i_slider.bind("<Button-5>", self._on_i_wheel)
        self.k_mute_i = ttk.Button(
            self.mix_panel,
            text="Mute",
            style="Mute.TButton",
            command=self.cb.on_i_mute,
            width=self._button_width,
        )
        self.k_mute_i.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(8, 0))
        self.k_full_i = ttk.Button(
            self.mix_panel, text="100%", command=self.cb.on_i_full, width=self._button_width
        )
        self.k_full_i.grid(row=1, column=3, sticky="ew", pady=(8, 0))

    def _build_tools(self, parent: tk.Frame, row: int) -> None:
        self.tools_panel = ttk.Frame(parent, style="Panel.TFrame", width=480)
        self.tools_panel.grid(row=row, column=1, padx=(14, 0), sticky="n")
        self.tools_panel.pack_propagate(False)
        for idx in range(8):
            self.tools_panel.columnconfigure(idx, weight=1)

        self.k_btn_font_smaller = ttk.Button(
            self.tools_panel,
            text="A-",
            command=self.cb.on_font_smaller,
            width=4,
        )
        self.k_btn_font_smaller.grid(row=0, column=0, padx=(10, 4), pady=(10, 6), sticky="ew")

        self.k_btn_font_larger = ttk.Button(
            self.tools_panel,
            text="A+",
            command=self.cb.on_font_larger,
            width=4,
        )
        self.k_btn_font_larger.grid(row=0, column=1, padx=4, pady=(10, 6), sticky="ew")

        self.k_btn_lines_fewer = ttk.Button(
            self.tools_panel,
            text="L-",
            command=self.cb.on_lines_fewer,
            width=4,
        )
        self.k_btn_lines_fewer.grid(row=0, column=2, padx=4, pady=(10, 6), sticky="ew")

        self.k_btn_lines_more = ttk.Button(
            self.tools_panel,
            text="L+",
            command=self.cb.on_lines_more,
            width=4,
        )
        self.k_btn_lines_more.grid(row=0, column=3, padx=4, pady=(10, 6), sticky="ew")

        self.k_btn_countdown_toggle = ttk.Button(
            self.tools_panel,
            text="",
            command=self.cb.on_toggle_countdown,
            width=11,
        )
        self.k_btn_countdown_toggle.grid(
            row=0,
            column=4,
            columnspan=2,
            padx=4,
            pady=(10, 6),
            sticky="ew",
        )

        self.k_btn_finish_toggle = ttk.Button(
            self.tools_panel,
            text="",
            command=self.cb.on_toggle_finish_celebration,
            width=10,
        )
        self.k_btn_finish_toggle.grid(
            row=0,
            column=6,
            columnspan=2,
            padx=(4, 10),
            pady=(10, 6),
            sticky="ew",
        )

        self.k_btn_loop_in = ttk.Button(
            self.tools_panel,
            text="Loop In",
            command=self.cb.on_loop_in,
            width=8,
        )
        self.k_btn_loop_in.grid(
            row=1,
            column=0,
            columnspan=2,
            padx=(10, 4),
            pady=(0, 10),
            sticky="ew",
        )

        self.k_btn_loop_out = ttk.Button(
            self.tools_panel,
            text="Loop Out",
            command=self.cb.on_loop_out,
            width=9,
        )
        self.k_btn_loop_out.grid(row=1, column=2, columnspan=2, padx=4, pady=(0, 10), sticky="ew")

        self.k_btn_loop_clear = ttk.Button(
            self.tools_panel,
            text="Clear loop",
            command=self.cb.on_loop_clear,
            width=6,
        )
        self.k_btn_loop_clear.grid(row=1, column=4, padx=4, pady=(0, 10), sticky="ew")

        self.k_loop_status = tk.Label(
            self.tools_panel,
            text="Loop off",
            bg=self.colors["panel"],
            fg=self.colors["muted"],
            font=("Fira Sans", 10),
            anchor="center",
        )
        self.k_loop_status.grid(row=1, column=5, columnspan=3, sticky="ew", padx=(8, 10), pady=(0, 10))
        self._update_toggle_button_texts()
        self._apply_loop_controls_state()

    def _build_scope(self, parent: tk.Frame, row: int) -> None:
        self.scope_panel = ttk.Frame(parent, style="Panel.TFrame", width=self._panel_width)
        self.scope_panel.grid(row=row, column=0, pady=(14, 0))
        self.scope_panel.pack_propagate(False)
        self.scope_panel.columnconfigure(0, weight=1)

        ttk.Label(self.scope_panel, text="Vocals (next 10s)", style="Subtle.TLabel").grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 4)
        )
        self.k_scope = tk.Canvas(
            self.scope_panel,
            height=80,
            bg=self.colors["panel"],
            highlightthickness=1,
            highlightbackground=self.colors["panel_border"],
        )
        self.k_scope.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

    def _ensure_record_panel(self) -> None:
        if self.record_panel is not None:
            return
        if self._record_parent is None:
            return
        self._build_record(self._record_parent, row=self._record_row)

    def _build_record(self, parent: tk.Frame, row: int) -> None:
        record_panel = ttk.Frame(parent, style="Panel.TFrame", width=self._panel_width)
        record_panel.grid(row=row, column=0, pady=(14, 0))
        record_panel.columnconfigure(0, weight=1)
        record_panel.columnconfigure(1, weight=1)

        self.k_recording_status = ttk.Label(
            record_panel,
            text="",
            style="Subtle.TLabel",
            wraplength=1000,
            justify="left",
        )
        self.k_recording_status.grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(8, 4))

        self.k_btn_record_next = ttk.Button(
            record_panel,
            text="Next line",
            command=self.cb.on_record_next,
            width=self._button_width,
        )
        self.k_btn_record_next.grid(row=1, column=0, padx=(10, 6), pady=(4, 10))

        self.k_btn_record_break = ttk.Button(
            record_panel,
            text="End of instrumental",
            command=self.cb.on_record_break,
            width=self._button_wide,
        )
        self.k_btn_record_break.grid(row=1, column=1, padx=(6, 10), pady=(4, 10))
        self.record_panel = record_panel

    def _set_record_panel_visible(self, visible: bool) -> None:
        if not self.is_open():
            return
        if self.record_panel is None:
            return
        if visible:
            if not self.record_panel.winfo_ismapped():
                self.record_panel.grid()
        else:
            if self.record_panel.winfo_ismapped():
                self.record_panel.grid_remove()

    def _show_window(self) -> None:
        if not self.is_open():
            return
        win = self.window
        try:
            win.deiconify()
            win.lift()
            win.focus_force()
            win.attributes("-topmost", True)
            win.after(200, lambda: win.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _apply_resize(self, width: int, height: int) -> None:
        width = max(600, width - 120)
        self._current_line_width_px = width
        line_count = max(1, self._visible_line_count)
        max_slot_height = int((height * 0.58) / line_count)
        self._current_line_height_px = max(
            self._lyrics_line_height * 2 + self._lyrics_line_gap,
            min(190, max_slot_height),
        )
        for slot, canvas in zip(self._line_slots, self._line_canvases):
            slot.configure(
                width=self._current_line_width_px,
                height=self._current_line_height_px,
            )
            canvas.place(
                relx=0.5,
                rely=0.5,
                anchor="center",
                width=self._current_line_width_px,
                height=self._current_line_height_px,
            )
        self._invalidate_canvas_layouts()
        if self.k_recording_status is not None:
            self.k_recording_status.configure(wraplength=max(500, width - 80))

        panel_width = min(self._panel_width, max(640, width - 40))
        tools_width = min(max(380, int(panel_width * 0.46)), max(320, panel_width - 280))
        mix_width = max(260, panel_width - tools_width - 14)
        seek_len = max(240, panel_width - 260)
        vol_len = max(120, mix_width - 280)
        self.controls_panel.configure(width=panel_width)
        if self.mix_row is not None:
            self.mix_row.configure(width=panel_width)
        self.mix_panel.configure(width=mix_width)
        if self.tools_panel is not None:
            self.tools_panel.configure(width=tools_width)
        if self.scope_panel is not None:
            self.scope_panel.configure(width=panel_width)
            if self.k_scope is not None:
                self.k_scope.configure(width=max(200, panel_width - 40))
        if self.record_panel is not None:
            self.record_panel.configure(width=panel_width)
        self.k_seek.configure(length=seek_len)
        self.k_v_slider.configure(length=vol_len)
        self.k_i_slider.configure(length=vol_len)
        if self.k_song_title is not None:
            self.k_song_title.configure(wraplength=max(320, width - 60))

    def _invalidate_canvas_layouts(self) -> None:
        self._slot_layout_w = [0] * len(self._line_canvases)
        self._slot_layout_h = [0] * len(self._line_canvases)

    def _render_plain_slots(self, slot_lines: tuple[str, ...]) -> None:
        active_slot = self._active_slot_idx
        for idx in range(len(self._line_canvases)):
            tokens = self._split_tokens(slot_lines[idx] if idx < len(slot_lines) else "")
            color = self.colors["text"] if idx == active_slot else self.colors["muted"]
            self._render_slot_tokens(idx, tokens, color, 0, None, 0.0)

    def _render_word_slots(
        self,
        slot_lines: tuple[str, ...],
        words: list[str],
        sung_words: int,
        active_word_idx: int | None,
        active_word_progress: float,
    ) -> None:
        active_slot = self._active_slot_idx
        active_tokens = [token for token in words if token]
        active_done = max(0, min(int(sung_words), len(active_tokens)))
        active_idx = (
            int(active_word_idx)
            if active_word_idx is not None and 0 <= int(active_word_idx) < len(active_tokens)
            else None
        )
        active_progress = min(max(float(active_word_progress), 0.0), 1.0)

        for idx in range(len(self._line_canvases)):
            if idx == active_slot and active_tokens:
                self._render_slot_tokens(
                    idx,
                    active_tokens,
                    self.colors["text"],
                    active_done,
                    active_idx,
                    active_progress,
                )
                continue
            tokens = self._split_tokens(slot_lines[idx] if idx < len(slot_lines) else "")
            color = self.colors["text"] if idx == active_slot else self.colors["muted"]
            self._render_slot_tokens(idx, tokens, color, 0, None, 0.0)

    def _split_tokens(self, text: str) -> list[str]:
        return [token for token in str(text).split() if token]

    def _render_slot_tokens(
        self,
        slot_idx: int,
        tokens: list[str],
        base_color: str,
        sung_words: int,
        active_word_idx: int | None,
        active_word_progress: float,
    ) -> None:
        if not (0 <= slot_idx < len(self._line_canvases)):
            return
        canvas = self._line_canvases[slot_idx]
        layout_width = self._current_line_text_width()
        layout_height = self._current_line_height()
        needs_rebuild = (
            self._slot_tokens[slot_idx] != tokens
            or self._slot_base_colors[slot_idx] != base_color
            or self._slot_layout_w[slot_idx] != layout_width
            or self._slot_layout_h[slot_idx] != layout_height
        )
        if needs_rebuild:
            self._slot_word_items[slot_idx] = self._build_slot_word_items(
                canvas,
                tokens,
                base_color=base_color,
                layout_width=layout_width,
                layout_height=layout_height,
            )
            self._slot_tokens[slot_idx] = list(tokens)
            self._slot_base_colors[slot_idx] = base_color
            self._slot_layout_w[slot_idx] = layout_width
            self._slot_layout_h[slot_idx] = layout_height

        self._apply_slot_word_colors(
            canvas,
            self._slot_word_items[slot_idx],
            sung_words=sung_words,
            active_word_idx=active_word_idx,
            active_word_progress=active_word_progress,
        )

    def _build_slot_word_items(
        self,
        canvas: tk.Canvas,
        tokens: list[str],
        *,
        base_color: str,
        layout_width: int,
        layout_height: int,
    ) -> list[_CanvasWordItem]:
        canvas.delete("all")
        if not tokens:
            return []

        word_widths = [max(1, int(self._lyrics_font.measure(token))) for token in tokens]
        space_width = self._lyrics_space_width

        lines: list[list[int]] = []
        line_widths: list[int] = []
        current_line: list[int] = []
        current_width = 0
        for idx, token_width in enumerate(word_widths):
            if not current_line:
                current_line = [idx]
                current_width = token_width
                continue
            next_width = current_width + space_width + token_width
            if next_width > layout_width:
                lines.append(current_line)
                line_widths.append(current_width)
                current_line = [idx]
                current_width = token_width
                continue
            current_line.append(idx)
            current_width = next_width
        if current_line:
            lines.append(current_line)
            line_widths.append(current_width)

        items_by_idx: list[Optional[_CanvasWordItem]] = [None] * len(tokens)
        total_height = len(lines) * self._lyrics_line_height + max(0, len(lines) - 1) * self._lyrics_line_gap
        y = max(0, int((layout_height - total_height) / 2))
        frame_width = self._current_line_width()
        for line_idx, line in enumerate(lines):
            line_width = line_widths[line_idx]
            x = max(self._line_pad_x, int((frame_width - line_width) / 2))
            for pos, token_idx in enumerate(line):
                if pos > 0:
                    x += space_width
                token = tokens[token_idx]
                token_width = word_widths[token_idx]

                base_id = canvas.create_text(
                    x,
                    y,
                    text=token,
                    font=self._lyrics_font,
                    fill=base_color,
                    anchor="nw",
                )
                full_id = canvas.create_text(
                    x,
                    y,
                    text=token,
                    font=self._lyrics_font,
                    fill=self.colors["accent"],
                    anchor="nw",
                    state="hidden",
                )
                part_id = canvas.create_text(
                    x,
                    y,
                    text="",
                    font=self._lyrics_font,
                    fill=self.colors["karaoke"],
                    anchor="nw",
                    state="hidden",
                )
                items_by_idx[token_idx] = _CanvasWordItem(
                    base_id=base_id,
                    full_id=full_id,
                    part_id=part_id,
                    token=token,
                )

                x += token_width
            y += self._lyrics_line_height + self._lyrics_line_gap

        return [item for item in items_by_idx if item is not None]

    def _apply_slot_word_colors(
        self,
        canvas: tk.Canvas,
        items: list[_CanvasWordItem],
        *,
        sung_words: int,
        active_word_idx: int | None,
        active_word_progress: float,
    ) -> None:
        if not items:
            return
        sung_limit = max(0, min(int(sung_words), len(items)))
        active_idx = (
            int(active_word_idx)
            if active_word_idx is not None and 0 <= int(active_word_idx) < len(items)
            else None
        )
        progress = min(max(float(active_word_progress), 0.0), 1.0)

        for idx, item in enumerate(items):
            if idx < sung_limit:
                canvas.itemconfigure(item.base_id, state="hidden")
                canvas.itemconfigure(item.full_id, state="normal")
                canvas.itemconfigure(item.part_id, state="hidden", text="")
                continue
            if active_idx is not None and idx == active_idx:
                chars = min(len(item.token), max(0, int(len(item.token) * progress)))
                if chars >= len(item.token):
                    canvas.itemconfigure(item.base_id, state="hidden")
                    canvas.itemconfigure(item.full_id, state="normal")
                    canvas.itemconfigure(item.part_id, state="hidden", text="")
                elif chars > 0:
                    canvas.itemconfigure(item.base_id, state="normal")
                    canvas.itemconfigure(item.full_id, state="hidden")
                    canvas.itemconfigure(item.part_id, state="normal", text=item.token[:chars])
                else:
                    canvas.itemconfigure(item.base_id, state="normal")
                    canvas.itemconfigure(item.full_id, state="hidden")
                    canvas.itemconfigure(item.part_id, state="hidden", text="")
                continue
            canvas.itemconfigure(item.base_id, state="normal")
            canvas.itemconfigure(item.full_id, state="hidden")
            canvas.itemconfigure(item.part_id, state="hidden", text="")

    def _current_line_width(self) -> int:
        return max(320, int(self._current_line_width_px))

    def _current_line_height(self) -> int:
        return max(self._lyrics_line_height, int(self._current_line_height_px))

    def _current_line_text_width(self) -> int:
        return max(240, self._current_line_width() - self._line_pad_x * 2)

    def _format_time(self, sec: float) -> str:
        sec = max(0.0, sec)
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m:02d}:{s:02d}"

    def _scale_value_from_x(self, scale: ttk.Scale, x: int) -> float:
        width = max(1, scale.winfo_width())
        start = float(scale.cget("from"))
        end = float(scale.cget("to"))
        ratio = min(max(x / width, 0.0), 1.0)
        return start + (end - start) * ratio

    def _scale_step(self, scale: ttk.Scale, direction: int, step: float = 0.05) -> None:
        start = float(scale.cget("from"))
        end = float(scale.cget("to"))
        cur = float(scale.get())
        nxt = min(max(cur + direction * step, start), end)
        scale.set(nxt)

    def _wheel_direction(self, event) -> int:
        if getattr(event, "num", None) in (4, 5):
            return 1 if event.num == 4 else -1
        delta = getattr(event, "delta", 0)
        return 1 if delta > 0 else -1

    def _on_seek_drag(self, value: str) -> None:
        if self._ignore_seek_events:
            return
        self._seeking = True
        if self._clear_seek_job and self.window is not None:
            try:
                self.window.after_cancel(self._clear_seek_job)
            except tk.TclError:
                pass
        if self.window is not None:
            self._clear_seek_job = self.window.after(200, self._clear_seeking)
        self.cb.on_seek(float(value))

    def _clear_seeking(self) -> None:
        self._seeking = False
        self._clear_seek_job = None

    def _on_seek_click(self, event) -> str:
        if str(self.k_seek.cget("state")) == "disabled":
            return "break"
        self.k_seek.set(self._scale_value_from_x(self.k_seek, event.x))
        return "break"

    def _on_seek_motion(self, event) -> str:
        if str(self.k_seek.cget("state")) == "disabled":
            return "break"
        self.k_seek.set(self._scale_value_from_x(self.k_seek, event.x))
        return "break"

    def _on_v_gain(self, value: str) -> None:
        if self._ignore_gain_events:
            return
        self.cb.on_v_gain(float(value))

    def _on_i_gain(self, value: str) -> None:
        if self._ignore_gain_events:
            return
        self.cb.on_i_gain(float(value))

    def _on_v_click(self, event) -> str:
        if str(self.k_v_slider.cget("state")) == "disabled":
            return "break"
        self.k_v_slider.set(self._scale_value_from_x(self.k_v_slider, event.x))
        return "break"

    def _on_v_motion(self, event) -> str:
        if str(self.k_v_slider.cget("state")) == "disabled":
            return "break"
        self.k_v_slider.set(self._scale_value_from_x(self.k_v_slider, event.x))
        return "break"

    def _on_i_click(self, event) -> str:
        if str(self.k_i_slider.cget("state")) == "disabled":
            return "break"
        self.k_i_slider.set(self._scale_value_from_x(self.k_i_slider, event.x))
        return "break"

    def _on_i_motion(self, event) -> str:
        if str(self.k_i_slider.cget("state")) == "disabled":
            return "break"
        self.k_i_slider.set(self._scale_value_from_x(self.k_i_slider, event.x))
        return "break"

    def _on_v_wheel(self, event) -> str:
        if str(self.k_v_slider.cget("state")) == "disabled":
            return "break"
        self._scale_step(self.k_v_slider, self._wheel_direction(event))
        return "break"

    def _on_i_wheel(self, event) -> str:
        if str(self.k_i_slider.cget("state")) == "disabled":
            return "break"
        self._scale_step(self.k_i_slider, self._wheel_direction(event))
        return "break"
