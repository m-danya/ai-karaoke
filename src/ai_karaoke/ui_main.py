from __future__ import annotations

import json
import os
import queue
import shutil
from bisect import bisect_right
from dataclasses import dataclass
import random
import re
import signal
import subprocess
import sys
import threading
import tempfile
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable, Dict, List, Optional, TypedDict

import numpy as np
import sounddevice as sd
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .audio import compute_vocals_env, decode_mp3_to_float32, mix_stems_to_mp3, transpose_mp3
from .config import resolve_library_path, save_config
from .constants import GENIUS_TAG, INSTR_TAG, KARAOKE_TAG, VOCALS_TAG
from .karaoke_screen import KaraokeCallbacks, KaraokeScreen
from .library import (
    base_name_for_pair,
    genius_lyrics_path_for_pair,
    karaoke_path_for_pair,
    load_playlists,
    normalize_track_id,
    save_playlists,
    scan_folder,
    track_id_for_pair,
)
from .models import SongPair
from .player import PlaybackController


class KaraokeWord(TypedDict):
    word: str
    start_ts: float
    end_ts: float


class KaraokeEntry(TypedDict):
    line: str
    start_ts: float
    end_ts: float
    words: List[KaraokeWord]


@dataclass(frozen=True)
class TrackListItem:
    track_id: str
    label: str
    pair: Optional[SongPair]
    missing: bool


@dataclass(frozen=True)
class TransposedTrackPaths:
    base_name: str
    vocals: Path
    instrumental: Path
    genius_lyrics: Path
    karaoke: Path


@dataclass(frozen=True)
class ExportMixSettings:
    label: str
    vocals_gain: float
    instr_gain: float
    vocals_muted: bool
    instr_muted: bool


_KEY_INPUT_RE = re.compile(r"^\s*([A-Ga-g])([#b]?)(m?)\s*$")
_SHARP_KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
_KEY_NAME_TO_INDEX = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}


def _parse_preview_key(raw: str) -> Optional[tuple[int, bool, bool]]:
    match = _KEY_INPUT_RE.fullmatch(raw.strip())
    if match is None:
        return None
    note = match.group(1).upper() + match.group(2)
    index = _KEY_NAME_TO_INDEX.get(note)
    if index is None:
        return None
    return index, bool(match.group(3)), match.group(2) == "b"


def _transpose_preview_key(raw: str, semitones: int) -> str:
    parsed = _parse_preview_key(raw)
    if parsed is None:
        return ""
    index, is_minor, prefer_flats = parsed
    names = _FLAT_KEY_NAMES if prefer_flats else _SHARP_KEY_NAMES
    note = names[(index + semitones) % 12]
    return f"{note}{'m' if is_minor else ''}"


def _transpose_suffix(semitones: int, attempt: int = 0) -> str:
    if attempt <= 0:
        return f"({semitones:+d} transposed)"
    return f"({semitones:+d} transposed {attempt + 1})"


def _build_transposed_track_paths(pair: SongPair, semitones: int, attempt: int = 0) -> TransposedTrackPaths:
    base_name = f"{base_name_for_pair(pair)} {_transpose_suffix(semitones, attempt)}"
    return TransposedTrackPaths(
        base_name=base_name,
        vocals=pair.vocals.with_name(f"{base_name}{VOCALS_TAG}.mp3"),
        instrumental=pair.instrumental.with_name(f"{base_name}{INSTR_TAG}.mp3"),
        genius_lyrics=pair.vocals.with_name(f"{base_name}{GENIUS_TAG}.txt"),
        karaoke=pair.vocals.with_name(f"{base_name}{KARAOKE_TAG}.json"),
    )


class App(tk.Tk):
    _FILTER_ALL = "All"
    _FILTER_HISTORY = "History"
    _RESERVED_FILTERS = {_FILTER_ALL, _FILTER_HISTORY}
    _MIN_KARAOKE_LOOP_SECONDS = 0.25
    _KARAOKE_FINISH_SOUND = (
        Path(__file__).resolve().parents[2]
        / "data/dragon-studio-crowd-cheer-and-applause-406644.mp3"
    )

    def __init__(
        self,
        folder: Path,
        invalid_path: Optional[str] = None,
        config: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(className="AIKaraoke")
        self.title("AI Karaoke")
        self.geometry("920x560")
        self.minsize(1120, 827)

        self.folder = folder
        self._config: Dict[str, str] = dict(config) if config is not None else {}
        self._config["library_path"] = str(folder)
        self._karaoke_font_size = self._load_karaoke_font_size()
        self._karaoke_visible_lines = self._load_karaoke_visible_lines()
        self._karaoke_countdown_enabled = self._load_karaoke_countdown_enabled()
        self._karaoke_finish_celebration_enabled = self._load_karaoke_finish_celebration_enabled()
        self._process_jobs = self._load_process_jobs()
        self._process_genius_delay_seconds = self._load_process_genius_delay_seconds()
        self._process_only_align = self._load_process_only_align()
        self.library_var = tk.StringVar(value=str(folder))
        self._all_pairs: List[SongPair] = []
        self._pairs_by_track_id: Dict[str, SongPair] = {}
        self._playlists, self._history = load_playlists(folder)
        self.items: List[TrackListItem] = []

        self.player = PlaybackController()
        self._ui_update_job: Optional[str] = None
        self._load_token = 0
        self._autoplay_after_load = False
        self._current_index: Optional[int] = None
        self._current_pair: Optional[SongPair] = None
        self._current_track_id: Optional[str] = None
        self._loading = False
        self._autoplay_armed = False
        self._last_playing = False
        self._ignore_gain_events = False
        self._ignore_select_event = False
        self._track_context_menu: Optional[tk.Menu] = None
        self._vocals_env: Optional[np.ndarray] = None
        self._vocals_env_hop = max(1, int(self.player.sr * 0.05))
        self._vocals_env_max = 1.0
        self._scope_window_sec = 10.0
        self._recording_active = False
        self._recording_lines: List[str] = []
        self._recording_index = 0
        self._recording_karaoke: List[Dict[str, float]] = []
        self._recording_pair: Optional[SongPair] = None
        self._recording_window: Optional[tk.Toplevel] = None
        self._recording_done_message: Optional[str] = None
        self._recording_done_job: Optional[str] = None
        self._process_running = False
        self._process_subprocess: Optional[subprocess.Popen[str]] = None
        self._process_reader_thread: Optional[threading.Thread] = None
        self._process_output_queue: Optional[queue.Queue[tuple[str, object]]] = None
        self._process_poll_job: Optional[str] = None
        self._process_log_window: Optional[tk.Toplevel] = None
        self._process_log_label: Optional[ttk.Label] = None
        self._process_log_text: Optional[tk.Text] = None
        self._process_settings_window: Optional[tk.Toplevel] = None
        self._transpose_running = False
        self._transpose_dialog: Optional[tk.Toplevel] = None
        self._transpose_progress_window: Optional[tk.Toplevel] = None
        self._transpose_progress_label: Optional[ttk.Label] = None
        self._transpose_progress_bar: Optional[ttk.Progressbar] = None
        self._transpose_thread: Optional[threading.Thread] = None
        self._save_mp3_running = False
        self._save_mp3_progress_window: Optional[tk.Toplevel] = None
        self._save_mp3_progress_label: Optional[ttk.Label] = None
        self._save_mp3_progress_bar: Optional[ttk.Progressbar] = None
        self._save_mp3_thread: Optional[threading.Thread] = None
        self._karaoke_entries: List[KaraokeEntry] = []
        self._karaoke_end_ts: List[float] = []
        self._karaoke_pair: Optional[SongPair] = None
        self._karaoke_idx_hint: Optional[int] = None
        self._karaoke_idx_hint_t = 0.0
        self._karaoke_countdown_value: Optional[int] = None
        self._karaoke_countdown_job: Optional[str] = None
        self._karaoke_finish_message: Optional[str] = None
        self._karaoke_finish_announced = False
        self._karaoke_finish_sound_pcm: Optional[np.ndarray] = None
        self._karaoke_finish_sound_load_failed = False
        self._karaoke_loop_in: Optional[float] = None
        self._karaoke_loop_out: Optional[float] = None
        self._karaoke_loop_enabled = False
        self._karaoke_loop_message: Optional[str] = None
        self._karaoke_loop_message_job: Optional[str] = None

        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value=self._FILTER_ALL)
        self.autoplay_var = tk.BooleanVar(value=False)

        self._rescan_track_pairs()

        self._apply_theme()
        self._build_karaoke_screen()
        self._build_ui()
        self.bind_all("<Button-1>", self._on_global_left_click, add="+")
        self.bind_all("<Escape>", self._on_global_escape, add="+")
        self._refresh_filter_options()
        self._apply_filter()
        if invalid_path:
            messagebox.showwarning(
                "Library not found",
                f"Configured library path not found:\n{invalid_path}\n\nUsing:\n{self.folder}",
            )
        if self._current_pair is None:
            self._set_controls_state(False)
        self._start_ui_updater()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_theme(self) -> None:
        self.colors = {
            "bg": "#f4f1ec",
            "panel": "#ffffff",
            "panel_border": "#e3ddd4",
            "text": "#1f2328",
            "muted": "#6b645c",
            "accent": "#0d6b5f",
            "accent_dark": "#0b5a50",
            "hover": "#eee7dd",
            "trough": "#d9d1c6",
            "trough_light": "#e9e2d8",
            "karaoke": "#1b7f2a",
            "karaoke_dark": "#166824",
            "missing": "#b93a32",
        }

        self.configure(bg=self.colors["bg"])
        self.option_add("*Font", ("Fira Sans", 11))

        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("Card.TFrame", background=self.colors["panel"], borderwidth=1, relief="solid")

        style.configure(
            "TLabel",
            background=self.colors["bg"],
            foreground=self.colors["text"],
        )
        style.configure(
            "Header.TLabel",
            background=self.colors["bg"],
            foreground=self.colors["text"],
            font=("Playfair Display", 20, "bold"),
        )
        style.configure(
            "SongTitle.TLabel",
            background=self.colors["bg"],
            foreground=self.colors["text"],
            font=("Fira Sans", 18, "bold"),
        )
        style.configure(
            "Subtle.TLabel",
            background=self.colors["bg"],
            foreground=self.colors["muted"],
        )
        style.configure(
            "Section.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["muted"],
            font=("Fira Sans", 10, "bold"),
        )
        style.configure(
            "Panel.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["text"],
        )
        style.configure(
            "Panel.Subtle.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["muted"],
        )
        style.configure(
            "Panel.Section.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["muted"],
            font=("Fira Sans", 10, "bold"),
        )
        style.configure(
            "Panel.Status.TLabel",
            background=self.colors["panel"],
            foreground=self.colors["muted"],
            font=("Fira Sans", 10),
        )
        style.configure(
            "Panel.TCheckbutton",
            background=self.colors["panel"],
            foreground=self.colors["text"],
        )
        style.map(
            "Panel.TCheckbutton",
            background=[("active", self.colors["panel"])],
            foreground=[("disabled", self.colors["muted"])],
        )

        style.configure(
            "TButton",
            background=self.colors["panel"],
            foreground=self.colors["text"],
            borderwidth=1,
            relief="solid",
            padding=(12, 7),
        )
        style.map(
            "TButton",
            background=[("active", self.colors["hover"])],
            foreground=[("disabled", self.colors["muted"])],
        )
        style.configure(
            "Mute.TButton",
            background=self.colors["panel"],
            foreground=self.colors["text"],
            borderwidth=1,
            relief="solid",
            focuscolor=self.colors["panel"],
            highlightcolor=self.colors["panel"],
            padding=(12, 7),
        )
        style.map(
            "Mute.TButton",
            background=[("active", self.colors["hover"])],
            foreground=[("disabled", self.colors["muted"])],
            relief=[("pressed", "solid"), ("!pressed", "solid")],
        )
        style.configure(
            "MuteActive.TButton",
            background=self.colors["accent"],
            foreground="#ffffff",
            borderwidth=1,
            relief="solid",
            bordercolor=self.colors["accent"],
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent"],
            focuscolor=self.colors["accent"],
            highlightcolor=self.colors["accent"],
            padding=(12, 7),
        )
        style.map(
            "MuteActive.TButton",
            background=[("active", self.colors["accent_dark"])],
            relief=[("pressed", "solid"), ("!pressed", "solid")],
        )
        style.configure(
            "Accent.TButton",
            background=self.colors["accent"],
            foreground="#ffffff",
            borderwidth=0,
            padding=(14, 7),
        )
        style.map("Accent.TButton", background=[("active", self.colors["accent_dark"])])
        style.configure(
            "Karaoke.TButton",
            background=self.colors["karaoke"],
            foreground="#ffffff",
            borderwidth=0,
            padding=(14, 7),
        )
        style.map("Karaoke.TButton", background=[("active", self.colors["karaoke_dark"])])

        style.configure(
            "Ghost.TButton",
            background=self.colors["bg"],
            foreground=self.colors["accent"],
            borderwidth=1,
            relief="solid",
            padding=(12, 7),
        )
        style.map("Ghost.TButton", background=[("active", self.colors["hover"])])

        style.configure(
            "Seek.Horizontal.TScale",
            background=self.colors["panel"],
            troughcolor=self.colors["trough"],
            borderwidth=0,
        )
        style.configure(
            "Volume.Horizontal.TScale",
            background=self.colors["panel"],
            troughcolor=self.colors["trough_light"],
            borderwidth=0,
        )

    def _build_karaoke_screen(self) -> None:
        self.karaoke = KaraokeScreen(
            self,
            self.colors,
            lyrics_font_size=self._karaoke_font_size,
            visible_lines=self._karaoke_visible_lines,
            countdown_enabled=self._karaoke_countdown_enabled,
            finish_celebration_enabled=self._karaoke_finish_celebration_enabled,
            callbacks=KaraokeCallbacks(
                on_exit_request=self._exit_fullscreen,
                on_play_toggle=self._toggle_play_pause,
                on_seek=self._on_karaoke_seek,
                on_loop_in=self._on_karaoke_loop_in,
                on_loop_out=self._on_karaoke_loop_out,
                on_loop_clear=self._on_karaoke_loop_clear,
                on_v_gain=self._on_karaoke_v_gain,
                on_i_gain=self._on_karaoke_i_gain,
                on_v_mute=self._mute_vocals,
                on_i_mute=self._mute_instr,
                on_v_full=self._set_v_full,
                on_i_full=self._set_i_full,
                on_record_next=self._record_next_line,
                on_record_break=self._record_instrumental_end,
                on_font_smaller=self._on_karaoke_font_smaller,
                on_font_larger=self._on_karaoke_font_larger,
                on_lines_fewer=self._on_karaoke_lines_fewer,
                on_lines_more=self._on_karaoke_lines_more,
                on_toggle_countdown=self._on_toggle_karaoke_countdown,
                on_toggle_finish_celebration=self._on_toggle_karaoke_finish_celebration,
            ),
        )

    def _parse_bool_config(self, key: str, default: bool) -> bool:
        raw = self._config.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _load_karaoke_font_size(self) -> int:
        raw = self._config.get("karaoke_font_size")
        if raw is None:
            return 36
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 36
        return max(20, min(72, value))

    def _load_karaoke_visible_lines(self) -> int:
        raw = self._config.get("karaoke_visible_lines")
        if raw is None:
            return 3
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 3
        return max(1, min(7, value))

    def _load_karaoke_countdown_enabled(self) -> bool:
        return self._parse_bool_config("karaoke_countdown_enabled", True)

    def _load_karaoke_finish_celebration_enabled(self) -> bool:
        return self._parse_bool_config("karaoke_finish_celebration_enabled", True)

    def _load_process_jobs(self) -> int:
        raw = self._config.get("process_jobs")
        if raw is None:
            return 1
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 1
        return max(1, min(64, value))

    def _load_process_genius_delay_seconds(self) -> float:
        raw = self._config.get("process_genius_delay_seconds")
        if raw is None:
            return 30.0
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 30.0
        return max(0.0, value)

    def _load_process_only_align(self) -> bool:
        return self._parse_bool_config("process_only_align", False)

    def _save_config(self) -> None:
        save_config(self._config)

    def _save_library_path(self, folder: Path) -> None:
        self._config["library_path"] = str(folder)
        self._save_config()

    def _save_karaoke_font_size(self) -> None:
        self._config["karaoke_font_size"] = str(self._karaoke_font_size)
        self._save_config()

    def _save_karaoke_visible_lines(self) -> None:
        self._config["karaoke_visible_lines"] = str(self._karaoke_visible_lines)
        self._save_config()

    def _save_karaoke_countdown_enabled(self) -> None:
        self._config["karaoke_countdown_enabled"] = "1" if self._karaoke_countdown_enabled else "0"
        self._save_config()

    def _save_karaoke_finish_celebration_enabled(self) -> None:
        self._config["karaoke_finish_celebration_enabled"] = (
            "1" if self._karaoke_finish_celebration_enabled else "0"
        )
        self._save_config()

    def _save_process_jobs(self) -> None:
        self._config["process_jobs"] = str(self._process_jobs)
        self._save_config()

    def _save_process_genius_delay_seconds(self) -> None:
        self._config["process_genius_delay_seconds"] = str(self._process_genius_delay_seconds)
        self._save_config()

    def _save_process_only_align(self) -> None:
        self._config["process_only_align"] = "1" if self._process_only_align else "0"
        self._save_config()

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=18)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 14))

        ttk.Label(header, text="AI Karaoke", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Blend vocals and instrumental in real time",
            style="Subtle.TLabel",
        ).pack(anchor="w")

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, style="Panel.TFrame")
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(body, width=260, style="TFrame")
        right.pack(side="right", fill="y", padx=(16, 0))
        right.pack_propagate(False)

        left_inner = ttk.Frame(left, padding=14, style="Panel.TFrame")
        left_inner.pack(fill="both", expand=True)

        library_row = ttk.Frame(left_inner, style="Panel.TFrame")
        library_row.pack(fill="x")

        ttk.Label(library_row, text="Library", style="Panel.Subtle.TLabel").pack(side="left", anchor="w")

        self.library_entry = ttk.Entry(library_row, textvariable=self.library_var)
        self.library_entry.pack(side="left", fill="x", expand=True, padx=(10, 8))
        self.library_entry.bind("<Return>", lambda event: self._rescan_music())

        self.btn_rescan = ttk.Button(library_row, text="Rescan", command=self._rescan_music)
        self.btn_rescan.pack(side="right", padx=(8, 0))
        self.btn_process = ttk.Button(
            library_row,
            text="Process",
            style="Ghost.TButton",
            command=self._process_library,
        )
        self.btn_process.pack(side="right", padx=(8, 0))
        self.btn_open_library = ttk.Button(
            library_row,
            text="Open",
            style="Ghost.TButton",
            command=self._open_library_folder,
        )
        self.btn_open_library.pack(side="right", padx=(8, 0))
        self.btn_choose_library = ttk.Button(
            library_row,
            text="Choose",
            style="Ghost.TButton",
            command=self._choose_library_folder,
        )
        self.btn_choose_library.pack(side="right", padx=(8, 0))

        autoplay_row = ttk.Frame(left_inner, style="Panel.TFrame")
        autoplay_row.pack(fill="x", pady=(8, 2))
        self.autoplay_chk = ttk.Checkbutton(
            autoplay_row,
            text="Autoplay",
            style="Panel.TCheckbutton",
            variable=self.autoplay_var,
        )
        self.autoplay_chk.pack(side="left")

        ttk.Label(left_inner, text="Tracks", style="Panel.Section.TLabel").pack(anchor="w", pady=(12, 4))

        search_row = ttk.Frame(left_inner, style="Panel.TFrame")
        search_row.pack(fill="x", pady=(0, 8))

        self.search = ttk.Entry(search_row, textvariable=self.search_var)
        self.search.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", self._on_search_change)
        ttk.Label(search_row, text="Filter", style="Panel.Subtle.TLabel").pack(side="left", padx=(8, 6))
        self.filter_combo = ttk.Combobox(
            search_row,
            textvariable=self.filter_var,
            state="readonly",
            width=20,
        )
        self.filter_combo.pack(side="left")
        self.filter_combo.bind("<<ComboboxSelected>>", self._on_filter_change)

        list_frame = ttk.Frame(left_inner, style="Panel.TFrame")
        list_frame.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(
            list_frame,
            height=16,
            bg=self.colors["panel"],
            fg=self.colors["text"],
            selectbackground=self.colors["accent"],
            selectforeground="#ffffff",
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            activestyle="none",
            font=("Fira Sans", 11),
            selectmode="extended",
            exportselection=False,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Button-1>", self._on_single_click)
        self.listbox.bind("<Button-3>", self._on_list_right_click)
        self.listbox.bind("<Button-2>", self._on_list_right_click)

        transport = ttk.Frame(left_inner, style="Panel.TFrame")
        transport.pack(fill="x", pady=(12, 0))

        self.btn_play = ttk.Button(transport, text="Play", style="Accent.TButton", command=self._toggle_play_pause)
        self.btn_play.pack(side="left")

        self.time_lbl = ttk.Label(transport, text="00:00 / 00:00", style="Panel.Subtle.TLabel")
        self.time_lbl.pack(side="right")

        self.seek = ttk.Scale(
            left_inner,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            style="Seek.Horizontal.TScale",
            command=self._on_seek_drag,
        )
        self.seek.pack(fill="x", pady=(10, 0))
        self._seeking = False
        self.seek.bind("<Button-1>", self._on_seek_click)
        self.seek.bind("<B1-Motion>", self._on_seek_motion)

        right_inner = ttk.Frame(right, padding=14, style="Panel.TFrame")
        right_inner.pack(fill="x", anchor="n")

        ttk.Label(right_inner, text="Mix", style="Panel.Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(right_inner, text="Vocals", style="Panel.Subtle.TLabel").pack(anchor="w")
        self.v_slider = ttk.Scale(
            right_inner,
            from_=0.0,
            to=1.5,
            orient="horizontal",
            style="Volume.Horizontal.TScale",
            command=self._on_v_gain,
        )
        self.v_slider.set(self.player.mix.vocals_gain)
        self.v_slider.pack(fill="x", pady=(4, 12))
        self.v_slider.bind("<Button-1>", self._on_v_click)
        self.v_slider.bind("<B1-Motion>", self._on_v_motion)
        self.v_slider.bind("<MouseWheel>", self._on_v_wheel)
        self.v_slider.bind("<Button-4>", self._on_v_wheel)
        self.v_slider.bind("<Button-5>", self._on_v_wheel)

        v_btn_row = ttk.Frame(right_inner)
        v_btn_row.pack(fill="x", pady=(6, 0))
        self.mute_v = ttk.Button(v_btn_row, text="Mute", style="Mute.TButton", command=self._mute_vocals)
        self.mute_v.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.full_v = ttk.Button(v_btn_row, text="100%", command=self._set_v_full)
        self.full_v.pack(side="left", expand=True, fill="x")

        ttk.Label(right_inner, text="Instrumental", style="Panel.Subtle.TLabel").pack(anchor="w", pady=(8, 0))
        self.i_slider = ttk.Scale(
            right_inner,
            from_=0.0,
            to=1.5,
            orient="horizontal",
            style="Volume.Horizontal.TScale",
            command=self._on_i_gain,
        )
        self.i_slider.set(self.player.mix.instr_gain)
        self.i_slider.pack(fill="x", pady=(4, 12))
        self.i_slider.bind("<Button-1>", self._on_i_click)
        self.i_slider.bind("<B1-Motion>", self._on_i_motion)
        self.i_slider.bind("<MouseWheel>", self._on_i_wheel)
        self.i_slider.bind("<Button-4>", self._on_i_wheel)
        self.i_slider.bind("<Button-5>", self._on_i_wheel)

        i_btn_row = ttk.Frame(right_inner)
        i_btn_row.pack(fill="x", pady=(6, 0))
        self.mute_i = ttk.Button(i_btn_row, text="Mute", style="Mute.TButton", command=self._mute_instr)
        self.mute_i.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.full_i = ttk.Button(i_btn_row, text="100%", command=self._set_i_full)
        self.full_i.pack(side="left", expand=True, fill="x")
        self._update_mute_buttons()

        self.btn_genius = ttk.Button(right_inner, text="Find lyrics", style="Ghost.TButton", command=self._open_genius)
        self.btn_genius.pack(fill="x", pady=(10, 0))

        self.btn_show_file = ttk.Button(
            right_inner,
            text="Show the file",
            style="Ghost.TButton",
            command=self._show_current_track_file,
        )
        self.btn_show_file.pack(fill="x", pady=(8, 0))

        self.btn_transpose = ttk.Button(
            right_inner,
            text="Transpose",
            style="Ghost.TButton",
            command=self._open_transpose_dialog,
        )
        self.btn_transpose.pack(fill="x", pady=(8, 0))

        self.btn_save_mp3 = ttk.Button(
            right_inner,
            text="Save as mp3",
            style="Ghost.TButton",
            command=self._save_current_track_as_mp3,
        )
        self.btn_save_mp3.pack(fill="x", pady=(8, 0))

        self.btn_delete = ttk.Button(
            right_inner,
            text="Delete track",
            style="Ghost.TButton",
            command=self._delete_current_track,
        )
        self.btn_delete.pack(fill="x", pady=(8, 0))

        ttk.Label(right_inner, text="Vocals (next 10s)", style="Panel.Subtle.TLabel").pack(anchor="w", pady=(10, 4))
        self.vocal_scope = tk.Canvas(
            right_inner,
            height=80,
            bg=self.colors["panel"],
            highlightthickness=1,
            highlightbackground=self.colors["panel_border"],
        )
        self.vocal_scope.pack(fill="x")
        self.vocal_scope.bind("<Configure>", lambda event: self._draw_vocal_scopes())

        self.btn_start_karaoke = ttk.Button(
            right_inner,
            text="Start karaoke mode",
            style="Karaoke.TButton",
            command=self._start_karaoke_mode,
        )
        self._karaoke_btn_pack = {"fill": "x", "pady": (12, 6)}
        # self.btn_start_recording = ttk.Button(
        #     right_inner,
        #     text="Make recording for this track",
        #     style="Ghost.TButton",
        #     command=self._prompt_recording_text,
        # )
        # self.btn_start_recording.pack(fill="x")

        self._set_recording_ui_state(False)

    def _set_controls_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.btn_play.configure(state=state)
        self.btn_show_file.configure(state=state)
        self.btn_transpose.configure(state=state)
        self.btn_save_mp3.configure(state=state)
        self.btn_delete.configure(state=state)
        self.seek.configure(state=state)
        self.btn_start_karaoke.configure(state=state)
        if self.karaoke.is_open():
            self.karaoke.set_play_controls_enabled(enabled)

    def _set_recording_ui_state(self, active: bool) -> None:
        if self.karaoke.is_open():
            self.karaoke.set_record_controls_enabled(active)
        list_state = "disabled" if active else "normal"
        self.listbox.configure(state=list_state)
        self.search.configure(state=list_state)
        self.filter_combo.configure(state="disabled" if active else "readonly")
        self.library_entry.configure(state=list_state)
        self.btn_choose_library.configure(state=list_state)
        self.btn_open_library.configure(state=list_state)
        self.btn_rescan.configure(state=list_state)
        self.btn_process.configure(state="disabled" if active or self._process_running else "normal")
        self.btn_show_file.configure(state=list_state)
        self.btn_transpose.configure(state=list_state)
        self.btn_save_mp3.configure(state=list_state)
        self.btn_delete.configure(state=list_state)
        self.btn_start_karaoke.configure(state=list_state)
        self.seek.configure(state=list_state)
        self.btn_play.configure(state=list_state)

    def _on_search_change(self, *args) -> None:
        self._apply_filter()

    def _on_filter_change(self, event) -> None:
        self._apply_filter()

    def _rescan_track_pairs(self) -> None:
        self._all_pairs = scan_folder(self.folder)
        self._pairs_by_track_id = {track_id_for_pair(pair): pair for pair in self._all_pairs}

    def _save_playlists_data(self) -> bool:
        try:
            save_playlists(self.folder, self._playlists, self._history)
            return True
        except OSError as exc:
            messagebox.showerror(
                "Playlists save failed",
                f"Could not save playlists in:\n{self.folder}\n\n{exc}",
            )
            return False

    def _playlist_names(self) -> List[str]:
        return sorted(self._playlists.keys(), key=str.casefold)

    def _refresh_filter_options(self) -> None:
        values = [self._FILTER_ALL, self._FILTER_HISTORY, *self._playlist_names()]
        if hasattr(self, "filter_combo"):
            self.filter_combo.configure(values=values)
        current = self.filter_var.get().strip()
        if current not in values:
            self.filter_var.set(self._FILTER_ALL)

    def _active_filter(self) -> str:
        current = self.filter_var.get().strip()
        if current in self._RESERVED_FILTERS:
            return current
        if current in self._playlists:
            return current
        return self._FILTER_ALL

    def _playlist_name_exists(self, raw_name: str) -> Optional[str]:
        needle = raw_name.casefold()
        for name in self._playlists:
            if name.casefold() == needle:
                return name
        return None

    def _is_reserved_playlist_name(self, raw_name: str) -> bool:
        reserved = {name.casefold() for name in self._RESERVED_FILTERS}
        return raw_name.casefold() in reserved

    def _collect_filter_track_ids(self) -> List[str]:
        filter_name = self._active_filter()
        if filter_name == self._FILTER_ALL:
            return [track_id_for_pair(pair) for pair in self._all_pairs]
        if filter_name == self._FILTER_HISTORY:
            return list(self._history)
        return list(self._playlists.get(filter_name, []))

    def _missing_label(self, track_id: str) -> str:
        p = Path(track_id)
        try:
            rel = p.relative_to(self.folder)
            return f"{rel} [missing]"
        except ValueError:
            return f"{p} [missing]"

    def _build_track_item(self, track_id: str) -> TrackListItem:
        pair = self._pairs_by_track_id.get(track_id)
        if pair is not None:
            return TrackListItem(track_id=track_id, label=pair.key, pair=pair, missing=False)
        return TrackListItem(
            track_id=track_id,
            label=self._missing_label(track_id),
            pair=None,
            missing=True,
        )

    def _is_playable_item(self, item: TrackListItem) -> bool:
        return item.pair is not None and not item.missing

    def _find_item_index_by_track_id(self, track_id: str) -> Optional[int]:
        for idx, item in enumerate(self.items):
            if item.track_id == track_id:
                return idx
        return None

    def _select_single_index(self, idx: int) -> None:
        self._ignore_select_event = True
        try:
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.activate(idx)
            self.listbox.see(idx)
        finally:
            self._ignore_select_event = False

    def _set_empty_list_state(self) -> None:
        self.listbox.selection_clear(0, "end")
        self._load_token += 1
        self._loading = False
        self._reset_karaoke_loop()
        if self.player.playing or self.player.is_fading_out():
            self.player.pause()
        self.player.stop()
        self._current_index = None
        self._current_pair = None
        self._current_track_id = None
        self._autoplay_armed = False
        self._set_controls_state(False)
        self._update_karaoke_button_visibility()
        self.seek.configure(to=0.0)
        self.seek.set(0.0)
        self.time_lbl.configure(text="00:00 / 00:00")
        self._vocals_env = None
        self._draw_vocal_scopes()

    def _select_missing_item(self, idx: int, show_message: bool = False) -> None:
        if not self.items:
            self._set_empty_list_state()
            return
        idx = max(0, min(idx, len(self.items) - 1))
        item = self.items[idx]
        self._current_index = idx
        self._current_pair = None
        self._current_track_id = item.track_id
        self._select_single_index(idx)
        self._load_token += 1
        self._loading = False
        self._reset_karaoke_loop()
        if self.player.playing or self.player.is_fading_out():
            self.player.pause()
        self.player.stop()
        self._autoplay_armed = False
        self._set_controls_state(False)
        self._update_karaoke_button_visibility()
        self.seek.configure(to=0.0)
        self.seek.set(0.0)
        self.time_lbl.configure(text="00:00 / 00:00")
        self._vocals_env = None
        self._draw_vocal_scopes()
        if show_message:
            messagebox.showinfo("Track unavailable", "This track is missing and cannot be played.")

    def _selected_track_ids(self) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for idx in self.listbox.curselection():
            if idx < 0 or idx >= len(self.items):
                continue
            track_id = self.items[int(idx)].track_id
            if track_id in seen:
                continue
            seen.add(track_id)
            out.append(track_id)
        return out

    def _add_tracks_to_playlist(self, playlist_name: str, track_ids: List[str]) -> None:
        if not track_ids:
            return
        tracks = self._playlists.setdefault(playlist_name, [])
        changed = False
        for track_id in track_ids:
            normalized = normalize_track_id(track_id)
            if normalized in tracks:
                continue
            tracks.append(normalized)
            changed = True
        if not changed:
            return
        if not self._save_playlists_data():
            return
        self._refresh_filter_options()
        self._apply_filter()

    def _remove_tracks_from_active_filter(self, track_ids: List[str]) -> None:
        if not track_ids:
            return
        remove_ids = set(track_ids)
        active = self._active_filter()
        changed = False
        if active == self._FILTER_HISTORY:
            updated = [track_id for track_id in self._history if track_id not in remove_ids]
            changed = len(updated) != len(self._history)
            self._history = updated
        elif active != self._FILTER_ALL:
            tracks = self._playlists.get(active, [])
            updated = [track_id for track_id in tracks if track_id not in remove_ids]
            changed = len(updated) != len(tracks)
            self._playlists[active] = updated
        if not changed:
            return
        preserve_idx = self._current_index
        if not self._save_playlists_data():
            return
        self._apply_filter(preserve_index=preserve_idx)

    def _add_current_track_to_history(self) -> None:
        if self._current_track_id is None:
            return
        track_id = normalize_track_id(self._current_track_id)
        if track_id in self._history:
            self._history.remove(track_id)
        self._history.append(track_id)
        if not self._save_playlists_data():
            return
        if self._active_filter() == self._FILTER_HISTORY:
            self._apply_filter()

    def _prompt_new_playlist(self, track_ids: List[str]) -> None:
        raw_name = simpledialog.askstring("New playlist", "Playlist name:", parent=self)
        if raw_name is None:
            return
        name = raw_name.strip()
        if not name:
            messagebox.showerror("Invalid name", "Playlist name cannot be empty.")
            return
        if self._is_reserved_playlist_name(name):
            messagebox.showerror("Invalid name", f'"{name}" is a reserved filter name.')
            return
        existing = self._playlist_name_exists(name)
        playlist_name = existing or name
        created = playlist_name not in self._playlists
        if created:
            self._playlists[playlist_name] = []
        changed = created
        tracks = self._playlists[playlist_name]
        for track_id in track_ids:
            normalized = normalize_track_id(track_id)
            if normalized in tracks:
                continue
            tracks.append(normalized)
            changed = True
        if not changed:
            return
        if not self._save_playlists_data():
            return
        self._refresh_filter_options()
        self._apply_filter()

    def _dismiss_track_context_menu(self) -> None:
        menu = self._track_context_menu
        if menu is None:
            return
        self._track_context_menu = None
        try:
            menu.unpost()
        except tk.TclError:
            pass
        try:
            menu.destroy()
        except tk.TclError:
            pass

    def _run_context_menu_action(self, action: Callable[[], None]) -> None:
        self._dismiss_track_context_menu()
        action()

    def _on_global_left_click(self, event) -> None:
        if self._track_context_menu is None:
            return
        if isinstance(event.widget, tk.Menu):
            return
        self._dismiss_track_context_menu()

    def _on_global_escape(self, event) -> None:
        self._dismiss_track_context_menu()

    def _show_track_context_menu(self, event, track_ids: List[str]) -> None:
        self._dismiss_track_context_menu()
        menu = tk.Menu(self, tearoff=0)
        self._track_context_menu = menu
        add_menu = tk.Menu(menu, tearoff=0)
        for playlist_name in self._playlist_names():
            add_menu.add_command(
                label=playlist_name,
                command=lambda name=playlist_name: self._run_context_menu_action(
                    lambda: self._add_tracks_to_playlist(name, track_ids)
                ),
            )
        if self._playlist_names():
            add_menu.add_separator()
        add_menu.add_command(
            label="New playlist...",
            command=lambda: self._run_context_menu_action(lambda: self._prompt_new_playlist(track_ids)),
        )
        menu.add_cascade(label="Add to playlist", menu=add_menu)
        active = self._active_filter()
        if active != self._FILTER_ALL:
            menu.add_separator()
            menu.add_command(
                label=f"Remove from {active}",
                command=lambda: self._run_context_menu_action(
                    lambda: self._remove_tracks_from_active_filter(track_ids)
                ),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _on_list_right_click(self, event) -> str:
        if str(self.listbox.cget("state")) == "disabled":
            return "break"
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self.items):
            return "break"
        bbox = self.listbox.bbox(idx)
        if bbox is None:
            return "break"
        y0 = bbox[1]
        y1 = y0 + bbox[3]
        if event.y < y0 or event.y > y1:
            return "break"
        current_selection = set(self.listbox.curselection())
        if idx not in current_selection:
            self._select_single_index(idx)
        track_ids = self._selected_track_ids()
        if not track_ids:
            return "break"
        self._show_track_context_menu(event, track_ids)
        return "break"

    def _apply_filter(self, preserve_index: Optional[int] = None) -> None:
        query = self.search_var.get().strip().casefold()
        track_ids = self._collect_filter_track_ids()
        items: List[TrackListItem] = []
        for track_id in track_ids:
            item = self._build_track_item(track_id)
            if query:
                haystack = f"{item.label}\n{item.track_id}".casefold()
                if query not in haystack:
                    continue
            items.append(item)
        self.items = items
        self._rebuild_list(preserve_index=preserve_index)

    def _rebuild_list(self, preserve_index: Optional[int] = None) -> None:
        self.listbox.delete(0, "end")
        for item in self.items:
            self.listbox.insert("end", item.label)
        self._apply_list_colors()

        if not self.items:
            self._set_empty_list_state()
            return

        if self._current_track_id is not None:
            current_idx = self._find_item_index_by_track_id(self._current_track_id)
            if current_idx is not None:
                item = self.items[current_idx]
                self._current_index = current_idx
                if self._is_playable_item(item):
                    self._select_single_index(current_idx)
                    if self._current_pair is None:
                        self._load_pair(current_idx)
                    else:
                        if not self._loading:
                            self._set_controls_state(True)
                        self._update_karaoke_button_visibility()
                    return
                self._select_missing_item(current_idx, show_message=False)
                return

        if preserve_index is not None:
            idx = max(0, min(preserve_index, len(self.items) - 1))
            if self._is_playable_item(self.items[idx]):
                self._load_pair(idx)
                return
            for i in range(idx + 1, len(self.items)):
                if self._is_playable_item(self.items[i]):
                    self._load_pair(i)
                    return
            for i in range(idx - 1, -1, -1):
                if self._is_playable_item(self.items[i]):
                    self._load_pair(i)
                    return

        for idx, item in enumerate(self.items):
            if self._is_playable_item(item):
                self._load_pair(idx)
                return

        self._select_missing_item(0, show_message=False)

    def _resolve_library_folder_from_input(self, *, allow_create: bool) -> Optional[Path]:
        raw = self.library_var.get().strip()
        if not raw:
            messagebox.showerror("Invalid folder", "Library path is empty.")
            return None
        folder = resolve_library_path(raw)
        if folder.exists() and not folder.is_dir():
            messagebox.showerror("Invalid folder", f"Path is not a folder:\n{folder}")
            return None
        if allow_create:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror(
                    "Cannot create folder",
                    f"Could not create folder:\n{folder}\n\n{exc}",
                )
                return None
        elif not folder.exists():
            messagebox.showerror("Invalid folder", f"Path does not exist:\n{folder}")
            return None
        return folder

    def _choose_library_folder(self) -> None:
        initial_dir = self.library_var.get().strip() or str(self.folder)
        chosen = filedialog.askdirectory(parent=self, initialdir=initial_dir, mustexist=True)
        if not chosen:
            return
        self.library_var.set(chosen)
        self._rescan_music()

    def _open_library_folder(self) -> None:
        folder = self._resolve_library_folder_from_input(allow_create=False)
        if folder is None:
            return
        self.library_var.set(str(folder))
        try:
            self._show_in_file_manager(folder)
        except OSError as exc:
            messagebox.showerror(
                "Open folder failed",
                f"Could not open folder in file manager:\n{folder}\n\n{exc}",
            )

    def _open_process_log_window(self, folder: Path) -> None:
        if self._process_log_window is None or not self._process_log_window.winfo_exists():
            win = tk.Toplevel(self)
            self._process_log_window = win
            win.title("Music processing log")
            win.geometry("860x520")
            win.transient(self)
            win.protocol("WM_DELETE_WINDOW", self._on_process_log_close)

            self._process_log_label = ttk.Label(
                win,
                text=f"Processing folder:\n{folder}",
                style="Subtle.TLabel",
                justify="left",
            )
            self._process_log_label.pack(anchor="w", padx=12, pady=(12, 8))

            text_frame = ttk.Frame(win)
            text_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

            text = tk.Text(
                text_frame,
                wrap="word",
                height=20,
                bg=self.colors["panel"],
                fg=self.colors["text"],
                highlightthickness=1,
                highlightbackground=self.colors["panel_border"],
            )
            text.pack(side="left", fill="both", expand=True)
            scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
            scroll.pack(side="right", fill="y")
            text.configure(yscrollcommand=scroll.set)
            self._process_log_text = text
        else:
            self._process_log_window.deiconify()
            self._process_log_window.lift()
            if self._process_log_label is not None and self._process_log_label.winfo_exists():
                self._process_log_label.configure(text=f"Processing folder:\n{folder}")
            text = self._process_log_text
            if text is not None and text.winfo_exists():
                text.delete("1.0", "end")

    def _append_process_log(self, line: str) -> None:
        text = self._process_log_text
        if text is None or not text.winfo_exists():
            return
        text.insert("end", line)
        text.see("end")

    def _close_process_log_window(self) -> None:
        if self._process_log_window is not None and self._process_log_window.winfo_exists():
            self._process_log_window.destroy()
        self._process_log_window = None
        self._process_log_label = None
        self._process_log_text = None

    def _ask_process_close_action(self) -> str:
        result = {"choice": "ok"}
        dialog = tk.Toplevel(self)
        dialog.title("Processing in progress")
        dialog.geometry("440x170")
        dialog.resizable(False, False)
        dialog.transient(self._process_log_window if self._process_log_window is not None else self)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=(
                "Processing is still running.\n"
                "Press OK to keep it running or Kill forcefully to stop it now."
            ),
            style="Subtle.TLabel",
            justify="left",
            wraplength=410,
        ).pack(anchor="w", padx=12, pady=(16, 12))

        button_row = ttk.Frame(dialog)
        button_row.pack(fill="x", padx=12, pady=(0, 12))

        def _choose(choice: str) -> None:
            result["choice"] = choice
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        ttk.Button(
            button_row,
            text="OK",
            command=lambda: _choose("ok"),
        ).pack(side="right", padx=(8, 0))
        ttk.Button(
            button_row,
            text="Kill forcefully",
            style="Ghost.TButton",
            command=lambda: _choose("kill"),
        ).pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _choose("ok"))
        self.wait_window(dialog)
        return str(result["choice"])

    def _close_process_settings_window(self) -> None:
        if self._process_settings_window is not None and self._process_settings_window.winfo_exists():
            try:
                self._process_settings_window.grab_release()
            except tk.TclError:
                pass
            self._process_settings_window.destroy()
        self._process_settings_window = None

    def _start_music_processing_with_settings(
        self,
        folder: Path,
        *,
        genius_delay_seconds: float,
        jobs: int,
        only_align: bool,
    ) -> None:
        self._process_genius_delay_seconds = genius_delay_seconds
        self._process_jobs = jobs
        self._process_only_align = only_align
        self._save_process_genius_delay_seconds()
        self._save_process_jobs()
        self._save_process_only_align()

        self._open_process_log_window(folder)
        self._append_process_log("Starting music processing pipeline...\n")

        command = [
            self._music_processing_python(),
            "-m",
            "ai_karaoke.music_processing.main",
            "--genius-delay-seconds",
            str(self._process_genius_delay_seconds),
            "-j",
            str(self._process_jobs),
        ]
        if self._process_only_align:
            command.append("--only-align")
        command.append(str(folder))

        self._append_process_log(f"Command: {' '.join(command)}\n\n")

        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "cwd": str(self._project_root()),
        }
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
        else:
            # Run in a separate session so force-kill can terminate all worker children.
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except (OSError, ValueError) as exc:
            self._append_process_log(f"Failed to start process: {exc}\n")
            return

        if process.stdout is None:
            self._append_process_log("Failed to capture process output.\n")
            self._terminate_music_processing_process(process, force_kill=False)
            return

        self._process_running = True
        self._process_subprocess = process
        self._process_output_queue = queue.Queue()
        self.btn_process.configure(state="disabled")

        def _reader() -> None:
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    output_queue = self._process_output_queue
                    if output_queue is None:
                        return
                    output_queue.put(("line", line))
            return_code = process.wait()
            output_queue = self._process_output_queue
            if output_queue is not None:
                output_queue.put(("done", return_code))

        self._process_reader_thread = threading.Thread(target=_reader, daemon=True)
        self._process_reader_thread.start()

        if self._process_poll_job is not None:
            try:
                self.after_cancel(self._process_poll_job)
            except tk.TclError:
                pass
        self._process_poll_job = self.after(100, self._poll_process_output)

    def _open_process_settings_window(self, folder: Path) -> None:
        if self._process_settings_window is not None and self._process_settings_window.winfo_exists():
            self._process_settings_window.deiconify()
            self._process_settings_window.lift()
            return

        win = tk.Toplevel(self)
        self._process_settings_window = win
        win.title("Process settings")
        win.geometry("460x250")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text=f"Target folder:\n{folder}",
            style="Subtle.TLabel",
            justify="left",
            wraplength=430,
        ).pack(anchor="w", padx=12, pady=(12, 10))

        form = ttk.Frame(win)
        form.pack(fill="x", padx=12)

        ttk.Label(form, text="Genius delay (sec):", style="Subtle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        delay_var = tk.StringVar(value=str(self._process_genius_delay_seconds))
        delay_entry = ttk.Entry(form, textvariable=delay_var, width=14)
        delay_entry.grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(form, text="Jobs (-j):", style="Subtle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        jobs_var = tk.StringVar(value=str(self._process_jobs))
        jobs_entry = ttk.Entry(form, textvariable=jobs_var, width=14)
        jobs_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        only_align_var = tk.BooleanVar(value=self._process_only_align)
        ttk.Checkbutton(
            form,
            text="Only align (skip separation + fetch)",
            variable=only_align_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        button_row = ttk.Frame(win)
        button_row.pack(fill="x", padx=12, pady=(14, 12))

        def _cancel() -> None:
            self._close_process_settings_window()

        def _start() -> None:
            try:
                delay = float(delay_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid setting", "Genius delay must be a number.")
                return
            if delay < 0:
                messagebox.showerror("Invalid setting", "Genius delay must be >= 0.")
                return

            try:
                jobs = int(jobs_var.get().strip())
            except ValueError:
                messagebox.showerror("Invalid setting", "Jobs must be an integer.")
                return
            if jobs < 1:
                messagebox.showerror("Invalid setting", "Jobs must be >= 1.")
                return

            only_align = bool(only_align_var.get())
            self._close_process_settings_window()
            self._start_music_processing_with_settings(
                folder,
                genius_delay_seconds=delay,
                jobs=jobs,
                only_align=only_align,
            )

        ttk.Button(button_row, text="Cancel", command=_cancel).pack(side="right", padx=(8, 0))
        ttk.Button(button_row, text="Start", style="Accent.TButton", command=_start).pack(
            side="right"
        )

        win.protocol("WM_DELETE_WINDOW", _cancel)
        delay_entry.focus_set()

    def _on_process_log_close(self) -> None:
        if not self._confirm_or_kill_running_process():
            return
        self._close_process_log_window()

    def _confirm_or_kill_running_process(self) -> bool:
        if not self._process_running:
            return True
        action = self._ask_process_close_action()
        if action != "kill":
            return False
        self._append_process_log("\nProcess forcefully killed by user.\n")
        self._stop_music_processing(force_kill=True)
        return True

    def _poll_process_output(self) -> None:
        self._process_poll_job = None
        output_queue = self._process_output_queue
        if output_queue is None:
            return

        return_code: Optional[int] = None
        while True:
            try:
                event, payload = output_queue.get_nowait()
            except queue.Empty:
                break

            if event == "line":
                self._append_process_log(str(payload))
                continue
            if event == "done":
                try:
                    return_code = int(payload)
                except (TypeError, ValueError):
                    return_code = 1
                break

        if return_code is not None:
            self._finish_process_run(return_code)
            return

        self._process_poll_job = self.after(100, self._poll_process_output)

    def _finish_process_run(self, return_code: int) -> None:
        self._process_running = False
        self._process_subprocess = None
        self._process_output_queue = None
        self._process_reader_thread = None
        if not self._recording_active:
            self.btn_process.configure(state="normal")

        if return_code == 0:
            self._append_process_log("\nProcess finished successfully.\n")
            self._rescan_music()
            return

        self._append_process_log(f"\nProcess failed with exit code {return_code}.\n")
        self._append_process_log(
            "If dependencies are missing, run: uv sync --group music-processing\n"
        )

    def _stop_music_processing(self, *, force_kill: bool = False) -> None:
        if self._process_poll_job is not None:
            try:
                self.after_cancel(self._process_poll_job)
            except tk.TclError:
                pass
            self._process_poll_job = None

        process = self._process_subprocess
        if process is not None and process.poll() is None:
            self._terminate_music_processing_process(process, force_kill=force_kill)

        self._process_running = False
        self._process_subprocess = None
        self._process_output_queue = None
        self._process_reader_thread = None
        if not self._recording_active:
            self.btn_process.configure(state="normal")

    def _terminate_music_processing_process(
        self, process: subprocess.Popen[str], *, force_kill: bool
    ) -> None:
        if process.poll() is not None:
            return

        if os.name != "nt":
            try:
                child_pgid = os.getpgid(process.pid)
            except (OSError, ProcessLookupError):
                child_pgid = None

            # Kill the child's own process group; never target current UI group.
            if child_pgid is not None and child_pgid != os.getpgrp():
                sig = signal.SIGKILL if force_kill else signal.SIGTERM
                try:
                    os.killpg(child_pgid, sig)
                except (OSError, ProcessLookupError):
                    return

                try:
                    process.wait(timeout=2)
                    return
                except subprocess.TimeoutExpired:
                    if force_kill:
                        return

                try:
                    os.killpg(child_pgid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                return

        if force_kill:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            return

        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _music_processing_python(self) -> str:
        project_root = self._project_root()
        if os.name == "nt":
            candidate = project_root / ".venv" / "Scripts" / "python.exe"
        else:
            candidate = project_root / ".venv" / "bin" / "python"
        if candidate.exists():
            return str(candidate)
        return sys.executable

    def _process_library(self) -> None:
        if self._recording_active:
            return
        if self._process_running:
            if self._process_log_window is not None and self._process_log_window.winfo_exists():
                self._process_log_window.deiconify()
                self._process_log_window.lift()
            return

        folder = self._resolve_library_folder_from_input(allow_create=False)
        if folder is None:
            return
        self.library_var.set(str(folder))
        self._save_library_path(folder)
        self._open_process_settings_window(folder)

    def _rescan_music(self) -> None:
        folder = self._resolve_library_folder_from_input(allow_create=True)
        if folder is None:
            return

        self.folder = folder
        self._save_library_path(folder)
        self.player.pause()
        self.player.stop()
        self._current_index = None
        self._current_pair = None
        self._current_track_id = None
        self._playlists, self._history = load_playlists(self.folder)
        self._rescan_track_pairs()
        self._refresh_filter_options()
        self._apply_filter()

    def _apply_list_colors(self) -> None:
        for idx, item in enumerate(self.items):
            if item.missing:
                color = self.colors["missing"]
            elif item.pair is not None and karaoke_path_for_pair(item.pair).exists():
                color = self.colors["karaoke"]
            else:
                color = self.colors["text"]
            self.listbox.itemconfig(idx, fg=color)

    def _has_karaoke_file(self, pair: Optional[SongPair]) -> bool:
        if pair is None:
            return False
        try:
            return karaoke_path_for_pair(pair).exists()
        except OSError:
            return False

    def _set_karaoke_button_visible(self, visible: bool) -> None:
        if visible:
            if not self.btn_start_karaoke.winfo_ismapped():
                self.btn_start_karaoke.pack(**self._karaoke_btn_pack)
        else:
            if self.btn_start_karaoke.winfo_ismapped():
                self.btn_start_karaoke.pack_forget()

    def _update_karaoke_button_visibility(self) -> None:
        self._set_karaoke_button_visible(self._has_karaoke_file(self._current_pair))

    def _delete_current_track(self) -> None:
        pair = self._current_pair
        if pair is None:
            messagebox.showinfo("No track selected", "Select a track to delete.")
            return

        confirm = messagebox.askyesno(
            "Delete track",
            "Delete the selected track files?\n\n"
            "This will permanently remove both MP3 stems and any related lyrics files.",
            icon="warning",
        )
        if not confirm:
            return

        if self.player.playing or self.player.is_fading_out():
            self.player.pause()
        self.player.stop()

        errors: List[str] = []
        for path in (
            pair.vocals,
            pair.instrumental,
            genius_lyrics_path_for_pair(pair),
            karaoke_path_for_pair(pair),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(f"{path}\n{exc}")

        if errors:
            messagebox.showerror("Delete failed", "Could not delete:\n\n" + "\n\n".join(errors))
            return

        old_idx = self._current_index or 0
        deleted_track_id = track_id_for_pair(pair)
        self._current_pair = None
        self._current_index = None
        self._current_track_id = deleted_track_id
        self._rescan_track_pairs()
        self._apply_filter(preserve_index=old_idx)

    def _format_time(self, sec: float) -> str:
        sec = max(0.0, sec)
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m:02d}:{s:02d}"

    def _load_pair(self, idx: int) -> None:
        if not self.items:
            return
        idx = max(0, min(idx, len(self.items) - 1))
        item = self.items[idx]
        if not self._is_playable_item(item):
            self._select_missing_item(idx, show_message=True)
            return
        pair = item.pair
        if pair is None:
            self._select_missing_item(idx, show_message=True)
            return

        self._current_index = idx
        self._current_track_id = item.track_id
        self._select_single_index(idx)

        self._current_pair = pair
        self._reset_karaoke_finish_state()
        self._reset_karaoke_loop()
        self._update_karaoke_button_visibility()
        self.player.pause()
        self.player.stop()
        self._autoplay_armed = False

        self._load_token += 1
        token = self._load_token
        self._loading = True
        self._set_controls_state(False)
        self.seek.configure(to=0.0)
        self.seek.set(0.0)
        self.time_lbl.configure(text="00:00 / 00:00")
        self._vocals_env = None
        self._vocals_env_max = 1.0
        self._draw_vocal_scopes()

        def worker() -> None:
            try:
                vocals = decode_mp3_to_float32(pair.vocals)
                instr = decode_mp3_to_float32(pair.instrumental)
                env, hop, env_max = compute_vocals_env(vocals, self.player.sr)
            except FileNotFoundError:
                self.after(0, self._on_missing_ffmpeg)
                return
            except subprocess.CalledProcessError as e:
                self.after(0, lambda: self._on_decode_error(pair, e))
                return

            self.after(0, lambda: self._finish_load(token, pair, vocals, instr, env, hop, env_max))

        threading.Thread(target=worker, daemon=True).start()

    def _on_missing_ffmpeg(self) -> None:
        messagebox.showerror("ffmpeg not found", "ffmpeg is required in PATH.")
        self._loading = False
        self.destroy()

    def _on_decode_error(self, pair: SongPair, err: Exception) -> None:
        messagebox.showerror("Decode error", f"Failed to decode:\n{pair.vocals}\n{pair.instrumental}\n\n{err}")
        self._loading = False
        if not pair.vocals.exists() or not pair.instrumental.exists():
            self._rescan_track_pairs()
            self._apply_filter(preserve_index=self._current_index)
            return
        if self._current_pair is not None:
            self._set_controls_state(True)

    def _finish_load(
        self,
        token: int,
        pair: SongPair,
        vocals: np.ndarray,
        instr: np.ndarray,
        env: np.ndarray,
        hop: int,
        env_max: float,
    ) -> None:
        if token != self._load_token:
            return
        self.player.load(vocals, instr)
        self._vocals_env = env
        self._vocals_env_hop = hop
        self._vocals_env_max = env_max
        dur = self.player.duration_seconds()
        self.seek.configure(to=dur)
        self.seek.set(0.0)
        self._set_controls_state(True)
        self._loading = False
        self._draw_vocal_scopes()

        if self.karaoke.is_open() and not self._recording_active:
            if self._has_karaoke_file(pair):
                self._load_karaoke_playback(pair, show_errors=False)
            else:
                self._clear_karaoke_playback()
            self._update_karaoke_ui()

        if self._autoplay_after_load:
            self._autoplay_after_load = False
            self.player.start()

    def _open_genius(self) -> None:
        pair = self._current_pair
        if pair is None:
            return
        cleaned = re.sub(r"[^0-9A-Za-zА-Яа-я]+", " ", pair.key).strip()
        if not cleaned:
            return
        query = urllib.parse.quote_plus(cleaned)
        url = f"https://www.google.com/search?q=genius+{query}"
        webbrowser.open(url, new=2)

    def _run_external_command(self, cmd: List[str]) -> bool:
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (FileNotFoundError, OSError, subprocess.CalledProcessError):
            return False

    def _show_in_file_manager(self, path: Path) -> None:
        target = path.resolve()
        if os.name == "nt":
            if self._run_external_command(["explorer", "/select,", str(target)]):
                return
            raise OSError("Could not launch Explorer.")

        if sys.platform.startswith("linux"):
            uri = target.as_uri()
            commands = [
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.freedesktop.FileManager1",
                    "--type=method_call",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{uri}",
                    "string:",
                ],
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.FileManager1",
                    "--object-path",
                    "/org/freedesktop/FileManager1",
                    "--method",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"[\"{uri}\"]",
                    "",
                ],
                ["nautilus", "--select", str(target)],
                ["dolphin", "--select", str(target)],
                ["nemo", str(target)],
                ["thunar", "--select", str(target)],
                ["pcmanfm", str(target)],
            ]
            for cmd in commands:
                if self._run_external_command(cmd):
                    return
            raise OSError("Could not launch a supported file manager.")

        raise OSError(f"Unsupported platform: {sys.platform}")

    def _show_current_track_file(self) -> None:
        pair = self._current_pair
        if pair is None:
            messagebox.showinfo("No track selected", "Select a track first.")
            return

        target = pair.instrumental
        if not target.exists():
            messagebox.showerror("File not found", f"File does not exist:\n{target}")
            return

        try:
            self._show_in_file_manager(target)
        except OSError as exc:
            messagebox.showerror(
                "Show file failed",
                f"Could not show file in file manager:\n{target}\n\n{exc}",
            )

    def _default_music_dir(self) -> Path:
        music_dir = (Path.home() / "Music").expanduser()
        if music_dir.exists():
            return music_dir
        return Path.home()

    def _current_mix_percentages(self) -> tuple[int, int]:
        vocals_gain = 0.0 if self.player.mix.vocals_muted else self.player.mix.vocals_gain
        instr_gain = 0.0 if self.player.mix.instr_muted else self.player.mix.instr_gain
        vocals_percent = int(round(max(0.0, vocals_gain) * 100.0))
        instr_percent = int(round(max(0.0, instr_gain) * 100.0))
        return vocals_percent, instr_percent

    def _current_mix_matches_original_levels(self) -> bool:
        vocals_percent, instr_percent = self._current_mix_percentages()
        return vocals_percent == 100 and instr_percent == 100

    def _ask_save_mp3_mix_mode(self) -> Optional[str]:
        if self._current_mix_matches_original_levels():
            return "original"

        vocals_percent, instr_percent = self._current_mix_percentages()
        result = {"choice": None}
        dialog = tk.Toplevel(self)
        dialog.title("Save as mp3")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=(
                "Choose volume levels mode.\n"
                "Original levels: 100% vocals, 100% instrumental.\n"
                f"Current UI levels: {vocals_percent}% vocals, {instr_percent}% instrumental."
            ),
            style="Subtle.TLabel",
            justify="left",
            wraplength=440,
        ).pack(anchor="w", padx=12, pady=(14, 12))

        button_row = ttk.Frame(dialog)
        button_row.pack(fill="x", padx=12, pady=(0, 14))

        def _choose(choice: Optional[str]) -> None:
            result["choice"] = choice
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

        current_button = ttk.Button(
            button_row,
            text="Current volume levels",
            style="Ghost.TButton",
            command=lambda: _choose("current"),
        )
        current_button.pack(side="right", padx=(8, 0))
        original_button = ttk.Button(
            button_row,
            text="Original volume levels (100% both)",
            style="Accent.TButton",
            command=lambda: _choose("original"),
        )
        original_button.pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", lambda: _choose(None))
        self._fit_dialog_to_content(dialog, min_width=470, min_height=160)
        original_button.focus_set()
        self.wait_window(dialog)
        choice = result["choice"]
        return choice if isinstance(choice, str) else None

    def _close_save_mp3_progress_window(self) -> None:
        progress = self._save_mp3_progress_bar
        if progress is not None:
            try:
                progress.stop()
            except tk.TclError:
                pass
        if self._save_mp3_progress_window is not None and self._save_mp3_progress_window.winfo_exists():
            try:
                self._save_mp3_progress_window.grab_release()
            except tk.TclError:
                pass
            self._save_mp3_progress_window.destroy()
        self._save_mp3_progress_window = None
        self._save_mp3_progress_label = None
        self._save_mp3_progress_bar = None

    def _set_save_mp3_progress(self, text: str) -> None:
        label = self._save_mp3_progress_label
        if label is None or not label.winfo_exists():
            return
        label.configure(text=text)
        try:
            label.update_idletasks()
        except tk.TclError:
            pass

    def _close_transpose_dialog(self) -> None:
        if self._transpose_dialog is not None and self._transpose_dialog.winfo_exists():
            try:
                self._transpose_dialog.grab_release()
            except tk.TclError:
                pass
            self._transpose_dialog.destroy()
        self._transpose_dialog = None

    def _close_transpose_progress_window(self) -> None:
        progress = self._transpose_progress_bar
        if progress is not None:
            try:
                progress.stop()
            except tk.TclError:
                pass
        if self._transpose_progress_window is not None and self._transpose_progress_window.winfo_exists():
            try:
                self._transpose_progress_window.grab_release()
            except tk.TclError:
                pass
            self._transpose_progress_window.destroy()
        self._transpose_progress_window = None
        self._transpose_progress_label = None
        self._transpose_progress_bar = None

    def _set_transpose_progress(self, text: str) -> None:
        label = self._transpose_progress_label
        if label is None or not label.winfo_exists():
            return
        label.configure(text=text)
        try:
            label.update_idletasks()
        except tk.TclError:
            pass

    def _ffmpeg_error_details(self, exc: Exception) -> str:
        if isinstance(exc, FileNotFoundError):
            return "ffmpeg is required in PATH."
        if isinstance(exc, subprocess.CalledProcessError):
            details = (exc.stderr or "").strip()
            if details:
                return details
        return str(exc)

    def _export_mix_settings(self, mode: str) -> ExportMixSettings:
        if mode == "current":
            return ExportMixSettings(
                label="current mix",
                vocals_gain=self.player.mix.vocals_gain,
                instr_gain=self.player.mix.instr_gain,
                vocals_muted=self.player.mix.vocals_muted,
                instr_muted=self.player.mix.instr_muted,
            )
        return ExportMixSettings(
            label="original 100/100 mix",
            vocals_gain=1.0,
            instr_gain=1.0,
            vocals_muted=False,
            instr_muted=False,
        )

    def _copy_related_file_if_present(self, source: Path, target: Path) -> bool:
        if not source.is_file():
            return False
        shutil.copy2(source, target)
        return True

    def _fit_dialog_to_content(
        self, win: tk.Toplevel, *, min_width: int, min_height: int
    ) -> None:
        self.update_idletasks()
        win.update_idletasks()
        width = max(win.winfo_reqwidth(), min_width)
        height = max(win.winfo_reqheight(), min_height)
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_w = self.winfo_width()
        parent_h = self.winfo_height()
        x = parent_x + max((parent_w - width) // 2, 0)
        y = parent_y + max((parent_h - height) // 2, 0)
        win.geometry(f"{width}x{height}+{x}+{y}")

    def _transposed_output_paths(self, pair: SongPair, semitones: int) -> TransposedTrackPaths:
        attempt = 0
        while True:
            paths = _build_transposed_track_paths(pair, semitones, attempt)
            targets = (paths.vocals, paths.instrumental, paths.genius_lyrics, paths.karaoke)
            if not any(path.exists() for path in targets):
                return paths
            attempt += 1

    def _save_current_track_as_mp3(self) -> None:
        if self._recording_active:
            return
        if self._loading:
            return
        if self._process_running:
            messagebox.showinfo(
                "Processing in progress",
                "Wait until library processing finishes before saving an MP3.",
            )
            return
        if self._transpose_running:
            messagebox.showinfo(
                "Transposition in progress",
                "Wait until the current transposition finishes before saving an MP3.",
            )
            return
        if self._save_mp3_running:
            if self._save_mp3_progress_window is not None and self._save_mp3_progress_window.winfo_exists():
                self._save_mp3_progress_window.deiconify()
                self._save_mp3_progress_window.lift()
            return

        pair = self._current_pair
        if pair is None:
            messagebox.showinfo("No track selected", "Select a track to save.")
            return
        if not pair.vocals.exists() or not pair.instrumental.exists():
            messagebox.showerror(
                "Track files missing",
                "The selected track is missing one or both MP3 stems. Rescan the library first.",
            )
            return

        mix_mode = self._ask_save_mp3_mix_mode()
        if mix_mode is None:
            return
        mix_settings = self._export_mix_settings(mix_mode)
        output_path = filedialog.asksaveasfilename(
            parent=self,
            title="Save as mp3",
            initialdir=str(self._default_music_dir()),
            initialfile=f"{base_name_for_pair(pair)}.mp3",
            defaultextension=".mp3",
            filetypes=[("MP3 files", "*.mp3")],
            confirmoverwrite=True,
        )
        if not output_path:
            return

        target = Path(output_path).expanduser()
        try:
            resolved_target = target.resolve(strict=False)
        except OSError:
            resolved_target = target.absolute()
        try:
            source_paths = {
                pair.vocals.resolve(strict=False),
                pair.instrumental.resolve(strict=False),
            }
        except OSError:
            source_paths = {pair.vocals.absolute(), pair.instrumental.absolute()}
        if resolved_target in source_paths:
            messagebox.showerror(
                "Invalid destination",
                "Choose a new file name so the export does not overwrite one of the source stems.",
            )
            return

        self._start_save_mp3(pair, resolved_target, mix_settings)

    def _open_transpose_dialog(self) -> None:
        if self._recording_active:
            return
        if self._loading:
            return
        if self._process_running:
            messagebox.showinfo(
                "Processing in progress",
                "Wait until library processing finishes before starting transposition.",
            )
            return
        if self._save_mp3_running:
            messagebox.showinfo(
                "MP3 export in progress",
                "Wait until the current MP3 export finishes before starting transposition.",
            )
            return
        if self._transpose_running:
            if self._transpose_progress_window is not None and self._transpose_progress_window.winfo_exists():
                self._transpose_progress_window.deiconify()
                self._transpose_progress_window.lift()
            return

        pair = self._current_pair
        if pair is None:
            messagebox.showinfo("No track selected", "Select a track to transpose.")
            return
        if not pair.vocals.exists() or not pair.instrumental.exists():
            messagebox.showerror(
                "Track files missing",
                "The selected track is missing one or both MP3 stems. Rescan the library first.",
            )
            return
        if self._transpose_dialog is not None and self._transpose_dialog.winfo_exists():
            self._transpose_dialog.deiconify()
            self._transpose_dialog.lift()
            return

        win = tk.Toplevel(self)
        self._transpose_dialog = win
        win.title("Transpose track")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text=(
                "Create a duplicate of the current track and shift both MP3 stems "
                "by the requested number of semitones."
            ),
            style="Subtle.TLabel",
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 10))

        form = ttk.Frame(win)
        form.pack(fill="x", padx=12)
        form.columnconfigure(1, weight=1)

        semitones_var = tk.StringVar(value="")
        original_key_var = tk.StringVar(value="")
        target_key_var = tk.StringVar(value="")

        ttk.Label(form, text="Semitones:", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
        semitones_entry = ttk.Entry(form, textvariable=semitones_var, width=16)
        semitones_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(form, text="Original key:", style="Subtle.TLabel").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        original_key_entry = ttk.Entry(form, textvariable=original_key_var, width=16)
        original_key_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(form, text="Transposed key:", style="Subtle.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(form, textvariable=target_key_var).grid(
            row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        ttk.Label(
            win,
            text="Original key is optional and used only for preview (examples: C, F#, Bb, Am).",
            style="Subtle.TLabel",
            wraplength=460,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 0))

        def _refresh_target_key(*_args) -> None:
            raw_key = original_key_var.get().strip()
            raw_semitones = semitones_var.get().strip()
            if not raw_key:
                target_key_var.set("")
                return
            try:
                semitones = int(raw_semitones)
            except ValueError:
                target_key_var.set("")
                return
            target = _transpose_preview_key(raw_key, semitones)
            target_key_var.set(target if target else "Invalid")

        semitones_var.trace_add("write", _refresh_target_key)
        original_key_var.trace_add("write", _refresh_target_key)

        button_row = ttk.Frame(win)
        button_row.pack(fill="x", padx=12, pady=(16, 12))

        def _cancel() -> None:
            self._close_transpose_dialog()

        def _start() -> None:
            raw_semitones = semitones_var.get().strip()
            try:
                semitones = int(raw_semitones)
            except ValueError:
                messagebox.showerror("Invalid semitones", "Semitones must be a whole number like -3 or +2.")
                return
            if semitones == 0:
                messagebox.showerror("Invalid semitones", "Semitones must be different from 0.")
                return

            self._close_transpose_dialog()
            self._start_transpose(pair, semitones)

        ttk.Button(button_row, text="Cancel", command=_cancel).pack(side="right", padx=(8, 0))
        ttk.Button(button_row, text="OK", style="Accent.TButton", command=_start).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", _cancel)
        self._fit_dialog_to_content(win, min_width=500, min_height=275)
        semitones_entry.focus_set()
        semitones_entry.bind("<Return>", lambda event: _start())
        original_key_entry.bind("<Return>", lambda event: _start())

    def _start_transpose(self, pair: SongPair, semitones: int) -> None:
        if self._transpose_running:
            return

        win = tk.Toplevel(self)
        self._transpose_progress_window = win
        win.title("Transposing")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        ttk.Label(
            win,
            text="Creating a transposed duplicate of the selected track.",
            style="Subtle.TLabel",
            wraplength=390,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(14, 8))

        self._transpose_progress_label = ttk.Label(win, text="Preparing...", style="Subtle.TLabel")
        self._transpose_progress_label.pack(anchor="w", padx=12)

        progress = ttk.Progressbar(win, mode="indeterminate", length=390)
        progress.pack(fill="x", padx=12, pady=(12, 14))
        progress.start(12)
        self._transpose_progress_bar = progress
        self._fit_dialog_to_content(win, min_width=430, min_height=150)

        self._transpose_running = True

        def worker() -> None:
            created_paths: List[Path] = []
            try:
                paths = self._transposed_output_paths(pair, semitones)
                self.after(0, lambda: self._set_transpose_progress("Transposing vocals..."))
                transpose_mp3(pair.vocals, paths.vocals, semitones)
                created_paths.append(paths.vocals)

                self.after(0, lambda: self._set_transpose_progress("Transposing instrumental..."))
                transpose_mp3(pair.instrumental, paths.instrumental, semitones)
                created_paths.append(paths.instrumental)

                self.after(0, lambda: self._set_transpose_progress("Copying lyrics..."))
                if self._copy_related_file_if_present(
                    genius_lyrics_path_for_pair(pair), paths.genius_lyrics
                ):
                    created_paths.append(paths.genius_lyrics)
                if self._copy_related_file_if_present(karaoke_path_for_pair(pair), paths.karaoke):
                    created_paths.append(paths.karaoke)
            except Exception as exc:
                for path in reversed(created_paths):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        pass
                self.after(0, lambda exc=exc: self._finish_transpose_failure(pair, exc))
                return

            self.after(0, lambda: self._finish_transpose_success(paths))

        self._transpose_thread = threading.Thread(target=worker, daemon=True)
        self._transpose_thread.start()

    def _finish_transpose_success(self, paths: TransposedTrackPaths) -> None:
        self._set_transpose_progress("Rescanning library...")
        new_track_id = normalize_track_id(paths.vocals)

        self._rescan_track_pairs()
        self._refresh_filter_options()
        self.filter_var.set(self._FILTER_ALL)
        self._current_index = None
        self._current_pair = None
        self._current_track_id = new_track_id
        self._apply_filter()
        if self._find_item_index_by_track_id(new_track_id) is None and self.search_var.get().strip():
            self.search_var.set("")

        self._transpose_running = False
        self._transpose_thread = None
        self._close_transpose_progress_window()
        messagebox.showinfo("Transpose complete", f"Created track:\n{paths.base_name}")

    def _finish_transpose_failure(self, pair: SongPair, exc: Exception) -> None:
        self._transpose_running = False
        self._transpose_thread = None
        self._close_transpose_progress_window()
        messagebox.showerror(
            "Transpose failed",
            f"Could not transpose:\n{pair.key}\n\n{self._ffmpeg_error_details(exc)}",
        )

    def _start_save_mp3(self, pair: SongPair, output_path: Path, mix_settings: ExportMixSettings) -> None:
        if self._save_mp3_running:
            return

        win = tk.Toplevel(self)
        self._save_mp3_progress_window = win
        win.title("Saving MP3")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        win.protocol("WM_DELETE_WINDOW", lambda: None)

        ttk.Label(
            win,
            text="Rendering the selected track into a single MP3 file.",
            style="Subtle.TLabel",
            wraplength=390,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(14, 8))

        self._save_mp3_progress_label = ttk.Label(
            win,
            text=f"Preparing {mix_settings.label}...",
            style="Subtle.TLabel",
        )
        self._save_mp3_progress_label.pack(anchor="w", padx=12)

        progress = ttk.Progressbar(win, mode="indeterminate", length=390)
        progress.pack(fill="x", padx=12, pady=(12, 14))
        progress.start(12)
        self._save_mp3_progress_bar = progress
        self._fit_dialog_to_content(win, min_width=430, min_height=150)

        self._save_mp3_running = True

        def worker() -> None:
            temp_output: Optional[Path] = None
            try:
                self.after(0, lambda: self._set_save_mp3_progress(f"Rendering {mix_settings.label}..."))
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
                    sr=self.player.sr,
                )
                temp_output.replace(output_path)
            except Exception as exc:
                if temp_output is not None:
                    try:
                        temp_output.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        pass
                self.after(0, lambda exc=exc: self._finish_save_mp3_failure(pair, output_path, exc))
                return

            self.after(0, lambda: self._finish_save_mp3_success(output_path))

        self._save_mp3_thread = threading.Thread(target=worker, daemon=True)
        self._save_mp3_thread.start()

    def _finish_save_mp3_success(self, output_path: Path) -> None:
        self._save_mp3_running = False
        self._save_mp3_thread = None
        self._close_save_mp3_progress_window()
        messagebox.showinfo("Save complete", f"Saved MP3 to:\n{output_path}")

    def _finish_save_mp3_failure(self, pair: SongPair, output_path: Path, exc: Exception) -> None:
        self._save_mp3_running = False
        self._save_mp3_thread = None
        self._close_save_mp3_progress_window()
        messagebox.showerror(
            "Save failed",
            f"Could not save MP3 for:\n{pair.key}\n\nDestination:\n{output_path}\n\n{self._ffmpeg_error_details(exc)}",
        )

    def _on_select(self, event) -> None:
        if self._ignore_select_event:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        if len(sel) != 1:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self.items):
            return
        item = self.items[idx]
        if not self._is_playable_item(item):
            self._select_missing_item(idx, show_message=False)
            return
        if self._loading and self._current_index == idx:
            return
        self._load_pair(idx)

    def _on_single_click(self, event) -> None:
        if self._recording_active:
            return
        idx = self.listbox.nearest(event.y)
        if idx < 0 or idx >= len(self.items):
            return
        bbox = self.listbox.bbox(idx)
        if bbox is None:
            return
        y0 = bbox[1]
        y1 = y0 + bbox[3]
        if event.y < y0 or event.y > y1:
            return
        item = self.items[idx]
        if self._current_index == idx and not self._loading and self._is_playable_item(item):
            self._toggle_play_pause()
            return "break"
        # Let the listbox update selection; _on_select will load the track.
        has_modifiers = bool(event.state & (0x0001 | 0x0004))
        self._autoplay_after_load = self._is_playable_item(item) and not has_modifiers

    def _toggle_play_pause(self) -> None:
        if self._recording_active or self._loading:
            return
        if self._current_pair is None:
            return
        if self._current_index is not None and self._current_index < len(self.items):
            if not self._is_playable_item(self.items[self._current_index]):
                return
        if self.player.playing and not self.player.is_fading_out():
            self.player.pause()
        else:
            if self._is_karaoke_loop_active():
                loop_in = float(self._karaoke_loop_in or 0.0)
                loop_out = float(self._karaoke_loop_out or loop_in)
                pos = self.player.position_seconds()
                if pos < loop_in or pos >= loop_out - 0.01:
                    self._seek_karaoke_position(loop_in, reset_finish=False)
            self.player.start()
            self._autoplay_armed = True

    def _on_v_gain(self, value: str) -> None:
        if self._ignore_gain_events:
            return
        self.player.set_vocals_gain(float(value), smooth=False)

    def _on_i_gain(self, value: str) -> None:
        if self._ignore_gain_events:
            return
        self.player.set_instr_gain(float(value), smooth=False)

    def _on_karaoke_v_gain(self, value: float) -> None:
        self.player.set_vocals_gain(float(value), smooth=False)
        self._sync_main_mix_controls()

    def _on_karaoke_i_gain(self, value: float) -> None:
        self.player.set_instr_gain(float(value), smooth=False)
        self._sync_main_mix_controls()

    def _mute_vocals(self) -> None:
        self.player.toggle_vocals_mute()
        self.v_slider.configure(state="disabled" if self.player.mix.vocals_muted else "normal")
        self._ignore_gain_events = True
        self.v_slider.set(self.player.mix.vocals_gain)
        self._ignore_gain_events = False
        self._update_mute_buttons()

    def _mute_instr(self) -> None:
        self.player.toggle_instr_mute()
        self.i_slider.configure(state="disabled" if self.player.mix.instr_muted else "normal")
        self._ignore_gain_events = True
        self.i_slider.set(self.player.mix.instr_gain)
        self._ignore_gain_events = False
        self._update_mute_buttons()

    def _set_v_full(self) -> None:
        self.player.set_vocals_full()
        self.v_slider.configure(state="normal")
        self._ignore_gain_events = True
        self.v_slider.set(self.player.mix.vocals_gain)
        self._ignore_gain_events = False
        self._update_mute_buttons()

    def _set_i_full(self) -> None:
        self.player.set_instr_full()
        self.i_slider.configure(state="normal")
        self._ignore_gain_events = True
        self.i_slider.set(self.player.mix.instr_gain)
        self._ignore_gain_events = False
        self._update_mute_buttons()

    def _update_mute_buttons(self) -> None:
        self.mute_v.configure(style="MuteActive.TButton" if self.player.mix.vocals_muted else "Mute.TButton")
        self.mute_i.configure(style="MuteActive.TButton" if self.player.mix.instr_muted else "Mute.TButton")

    def _sync_main_mix_controls(self) -> None:
        self._ignore_gain_events = True
        self.v_slider.set(self.player.mix.vocals_gain)
        self.i_slider.set(self.player.mix.instr_gain)
        self._ignore_gain_events = False
        self.v_slider.configure(state="disabled" if self.player.mix.vocals_muted else "normal")
        self.i_slider.configure(state="disabled" if self.player.mix.instr_muted else "normal")
        self._update_mute_buttons()

    def _on_seek_drag(self, value: str) -> None:
        # Tkinter scale calls this continuously; we mark seeking and apply position.
        self._seeking = True
        self.player.seek_seconds(float(value))
        # Let UI updater clear seeking after a short delay.
        self.after(200, self._clear_seeking)

    def _seek_karaoke_position(self, value: float, *, reset_finish: bool = True) -> float:
        self._karaoke_idx_hint = None
        self._karaoke_idx_hint_t = 0.0
        if reset_finish:
            self._reset_karaoke_finish_state()
        self.player.seek_seconds(float(value))
        pos = self.player.position_seconds()
        self.seek.set(pos)
        return pos

    def _on_karaoke_seek(self, value: float) -> None:
        self._seek_karaoke_position(value)

    def _on_karaoke_loop_in(self) -> None:
        if self._current_pair is None or self._loading:
            return
        self._karaoke_loop_in = self.player.position_seconds()
        self._karaoke_loop_out = None
        self._karaoke_loop_enabled = False
        self._clear_karaoke_loop_message()
        self._update_karaoke_ui()

    def _on_karaoke_loop_out(self) -> None:
        if self._current_pair is None or self._loading:
            return
        if self._karaoke_loop_in is None:
            self._set_karaoke_loop_message("Set In first.")
            return
        loop_out = self.player.position_seconds()
        if loop_out <= self._karaoke_loop_in + self._MIN_KARAOKE_LOOP_SECONDS:
            self._set_karaoke_loop_message("Out must be after In.")
            return
        self._karaoke_loop_out = loop_out
        self._karaoke_loop_enabled = True
        self._clear_karaoke_loop_message()
        self._seek_karaoke_position(self._karaoke_loop_in, reset_finish=False)
        self._update_karaoke_ui()

    def _on_karaoke_loop_clear(self) -> None:
        if self._karaoke_loop_in is None:
            return
        self._reset_karaoke_loop()
        self._update_karaoke_ui()

    def _adjust_karaoke_font_size(self, delta: int) -> None:
        target = max(20, min(72, self._karaoke_font_size + int(delta)))
        if target == self._karaoke_font_size:
            return
        self._karaoke_font_size = self.karaoke.set_lyrics_font_size(target)
        self._save_karaoke_font_size()
        self._update_karaoke_ui()

    def _on_karaoke_font_smaller(self) -> None:
        self._adjust_karaoke_font_size(-2)

    def _on_karaoke_font_larger(self) -> None:
        self._adjust_karaoke_font_size(2)

    def _adjust_karaoke_visible_lines(self, delta: int) -> None:
        target = max(1, min(7, self._karaoke_visible_lines + int(delta)))
        if target == self._karaoke_visible_lines:
            return
        self._karaoke_visible_lines = self.karaoke.set_visible_line_count(target)
        self._save_karaoke_visible_lines()
        self._update_karaoke_ui()

    def _on_karaoke_lines_fewer(self) -> None:
        self._adjust_karaoke_visible_lines(-1)

    def _on_karaoke_lines_more(self) -> None:
        self._adjust_karaoke_visible_lines(1)

    def _on_toggle_karaoke_countdown(self) -> None:
        self._karaoke_countdown_enabled = not self._karaoke_countdown_enabled
        self.karaoke.set_countdown_enabled(self._karaoke_countdown_enabled)
        self._save_karaoke_countdown_enabled()

    def _on_toggle_karaoke_finish_celebration(self) -> None:
        self._karaoke_finish_celebration_enabled = not self._karaoke_finish_celebration_enabled
        self.karaoke.set_finish_celebration_enabled(self._karaoke_finish_celebration_enabled)
        self._save_karaoke_finish_celebration_enabled()
        if not self._karaoke_finish_celebration_enabled:
            self._reset_karaoke_finish_state()
            self._update_karaoke_ui()

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

    def _on_seek_click(self, event) -> str:
        if str(self.seek.cget("state")) == "disabled":
            return "break"
        self.seek.set(self._scale_value_from_x(self.seek, event.x))
        return "break"

    def _on_seek_motion(self, event) -> str:
        if str(self.seek.cget("state")) == "disabled":
            return "break"
        self.seek.set(self._scale_value_from_x(self.seek, event.x))
        return "break"

    def _on_v_click(self, event) -> str:
        if str(self.v_slider.cget("state")) == "disabled":
            return "break"
        self.v_slider.set(self._scale_value_from_x(self.v_slider, event.x))
        return "break"

    def _on_v_motion(self, event) -> str:
        if str(self.v_slider.cget("state")) == "disabled":
            return "break"
        self.v_slider.set(self._scale_value_from_x(self.v_slider, event.x))
        return "break"

    def _on_i_click(self, event) -> str:
        if str(self.i_slider.cget("state")) == "disabled":
            return "break"
        self.i_slider.set(self._scale_value_from_x(self.i_slider, event.x))
        return "break"

    def _on_i_motion(self, event) -> str:
        if str(self.i_slider.cget("state")) == "disabled":
            return "break"
        self.i_slider.set(self._scale_value_from_x(self.i_slider, event.x))
        return "break"

    def _wheel_direction(self, event) -> int:
        if getattr(event, "num", None) in (4, 5):
            return 1 if event.num == 4 else -1
        delta = getattr(event, "delta", 0)
        return 1 if delta > 0 else -1

    def _on_v_wheel(self, event) -> str:
        if str(self.v_slider.cget("state")) == "disabled":
            return "break"
        self._scale_step(self.v_slider, self._wheel_direction(event))
        return "break"

    def _on_i_wheel(self, event) -> str:
        if str(self.i_slider.cget("state")) == "disabled":
            return "break"
        self._scale_step(self.i_slider, self._wheel_direction(event))
        return "break"

    def _clear_seeking(self) -> None:
        self._seeking = False

    def _draw_vocal_scope_canvas(self, canvas: tk.Canvas) -> None:
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w <= 2 or h <= 2:
            return

        canvas.delete("all")
        env = self._vocals_env
        if env is None or env.size == 0:
            return

        pos = self.player.position_seconds()
        hop = self._vocals_env_hop
        if hop <= 0:
            return

        start = int(pos * self.player.sr / hop)
        end = int((pos + self._scope_window_sec) * self.player.sr / hop)
        if end <= start:
            return

        if start >= env.size:
            segment = np.zeros(2, dtype=np.float32)
        else:
            segment = env[start:min(end, env.size)]
            target = end - start
            if segment.size < target:
                segment = np.pad(segment, (0, target - segment.size))
            if segment.size < 2:
                segment = np.pad(segment, (0, 2 - segment.size))

        n = max(2, int(w))
        x_src = np.linspace(0, segment.size - 1, num=segment.size, endpoint=True)
        x_tgt = np.linspace(0, segment.size - 1, num=n, endpoint=True)
        y = np.interp(x_tgt, x_src, segment)
        maxv = float(np.max(y)) if y.size else 1.0
        ref = self._vocals_env_max if self._vocals_env_max > 1e-6 else maxv
        if ref <= 1e-6:
            ref = 1.0
        y = y / ref
        y = np.clip(y, 0.0, 1.0)

        pad_y = 6
        base = h - pad_y
        amp = max(1, h - 2 * pad_y)
        points: List[float] = []
        for i, val in enumerate(y):
            x = float(i)
            ypix = base - (val * amp)
            points.extend([x, ypix])

        canvas.create_line(0, base, w, base, fill=self.colors["trough"])
        canvas.create_line(0, pad_y, 0, base, fill=self.colors["accent_dark"])
        canvas.create_line(points, fill=self.colors["accent"], width=2, smooth=True)

    def _draw_vocal_scope(self) -> None:
        if not hasattr(self, "vocal_scope"):
            return
        self._draw_vocal_scope_canvas(self.vocal_scope)

    def _draw_vocal_scopes(self) -> None:
        if hasattr(self, "vocal_scope"):
            self._draw_vocal_scope_canvas(self.vocal_scope)
        if self.karaoke.is_open():
            scope = self.karaoke.scope_canvas()
            if scope is not None:
                self._draw_vocal_scope_canvas(scope)

    def _start_ui_updater(self) -> None:
        def tick() -> None:
            self._ui_update_job = None
            if not self.winfo_exists():
                return
            self._update_ui()
            self._ui_update_job = self.after(50, tick)

        if self._ui_update_job is None:
            self._ui_update_job = self.after(50, tick)

    def _update_ui(self) -> None:
        dur = self.player.duration_seconds()
        pos = self.player.position_seconds()
        pos = self._apply_karaoke_loop(pos, dur)
        self.time_lbl.configure(text=f"{self._format_time(pos)} / {self._format_time(dur)}")
        if not self._seeking:
            self.seek.set(pos)
        self.btn_play.configure(
            text="Pause" if self.player.playing and not self.player.is_fading_out() else "Play"
        )
        self._update_karaoke_ui()
        if self.player.playing and not self._last_playing:
            self._autoplay_armed = True
            self._reset_karaoke_finish_state()
        if self._is_karaoke_track_finished(dur, pos):
            self._on_karaoke_finished()
        if (
            self.autoplay_var.get()
            and self._autoplay_armed
            and self._last_playing
            and not self.player.playing
            and not self.player.is_fading_out()
            and not self._loading
            and not self._recording_active
            and dur > 0.0
            and pos >= max(0.0, dur - 0.05)
        ):
            self._autoplay_armed = False
            self._play_next_track()
        if (
            self._recording_active
            and not self.player.playing
            and not self.player.is_fading_out()
            and not self._loading
            and dur > 0.0
            and pos >= max(0.0, dur - 0.05)
        ):
            self._append_remaining_recording_lines(max(pos, dur))
            self._finish_recording(save=True)
        self._last_playing = self.player.playing
        self._draw_vocal_scopes()

    def _is_track_end_reached(self, dur: float, pos: float) -> bool:
        return dur > 0.0 and pos >= max(0.0, dur - 0.05)

    def _is_karaoke_track_finished(self, dur: float, pos: float) -> bool:
        return (
            self._last_playing
            and not self.player.playing
            and not self.player.is_fading_out()
            and not self._loading
            and not self._recording_active
            and not self._karaoke_finish_announced
            and self.karaoke.is_open()
            and self.karaoke.mode == "play"
            and bool(self._karaoke_entries)
            and self._is_track_end_reached(dur, pos)
        )

    def _on_karaoke_finished(self) -> None:
        self._karaoke_finish_announced = True
        if not self._karaoke_finish_celebration_enabled:
            return
        score = random.randint(75, 99)
        self._karaoke_finish_message = f"Вы поёте великолепно! Баллы: {score}"
        self._play_karaoke_finish_sound()
        self._update_karaoke_ui()

    def _play_karaoke_finish_sound(self) -> None:
        path = self._KARAOKE_FINISH_SOUND
        if not path.exists():
            return
        try:
            subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            pass
        pcm = self._load_karaoke_finish_sound_pcm()
        if pcm is None or pcm.size == 0:
            return
        try:
            sd.play(pcm, samplerate=self.player.sr, blocking=False)
        except Exception:
            return

    def _load_karaoke_finish_sound_pcm(self) -> Optional[np.ndarray]:
        if self._karaoke_finish_sound_pcm is not None:
            return self._karaoke_finish_sound_pcm
        if self._karaoke_finish_sound_load_failed:
            return None
        path = self._KARAOKE_FINISH_SOUND
        if not path.exists():
            self._karaoke_finish_sound_load_failed = True
            return None
        try:
            self._karaoke_finish_sound_pcm = decode_mp3_to_float32(
                path, sr=self.player.sr, ch=self.player.ch
            )
        except (FileNotFoundError, subprocess.CalledProcessError, OSError):
            self._karaoke_finish_sound_load_failed = True
            return None
        return self._karaoke_finish_sound_pcm

    def _reset_karaoke_finish_state(self) -> None:
        self._karaoke_finish_message = None
        self._karaoke_finish_announced = False

    def _clear_karaoke_loop_message(self) -> None:
        if self._karaoke_loop_message_job is not None:
            try:
                self.after_cancel(self._karaoke_loop_message_job)
            except tk.TclError:
                pass
        self._karaoke_loop_message_job = None
        self._karaoke_loop_message = None

    def _set_karaoke_loop_message(self, text: str, ttl_ms: int = 1800) -> None:
        self._clear_karaoke_loop_message()
        self._karaoke_loop_message = text
        self._karaoke_loop_message_job = self.after(ttl_ms, self._clear_karaoke_loop_message)
        self._update_karaoke_ui()

    def _reset_karaoke_loop(self) -> None:
        self._karaoke_loop_in = None
        self._karaoke_loop_out = None
        self._karaoke_loop_enabled = False
        self._clear_karaoke_loop_message()

    def _is_karaoke_loop_active(self) -> bool:
        return (
            self.karaoke.is_open()
            and self.karaoke.mode == "play"
            and self._karaoke_loop_enabled
            and self._karaoke_loop_in is not None
            and self._karaoke_loop_out is not None
            and self._karaoke_loop_out > self._karaoke_loop_in + self._MIN_KARAOKE_LOOP_SECONDS
        )

    def _karaoke_loop_start_position(self) -> float:
        if self._is_karaoke_loop_active() and self._karaoke_loop_in is not None:
            return self._karaoke_loop_in
        return 0.0

    def _format_loop_time(self, sec: float) -> str:
        deciseconds = int(round(max(0.0, sec) * 10))
        minutes = deciseconds // 600
        seconds = (deciseconds % 600) / 10.0
        return f"{minutes:02d}:{seconds:04.1f}"

    def _karaoke_loop_status_text(self) -> str:
        if self._karaoke_loop_message:
            return self._karaoke_loop_message
        if self._karaoke_loop_in is None:
            return "Loop off"
        loop_in = self._format_loop_time(self._karaoke_loop_in)
        if not self._karaoke_loop_enabled or self._karaoke_loop_out is None:
            return f"In {loop_in}"
        loop_out = self._format_loop_time(self._karaoke_loop_out)
        return f"{loop_in} - {loop_out}"

    def _apply_karaoke_loop(self, pos: float, dur: float) -> float:
        if not self._is_karaoke_loop_active():
            return pos
        loop_in = float(self._karaoke_loop_in or 0.0)
        loop_out = float(self._karaoke_loop_out or loop_in)
        if pos < loop_out - 0.01:
            return pos
        track_ended = (
            self._last_playing
            and not self.player.playing
            and not self.player.is_fading_out()
            and self._is_track_end_reached(dur, pos)
        )
        if not self.player.playing and not track_ended:
            return pos
        pos = self._seek_karaoke_position(loop_in, reset_finish=False)
        if track_ended:
            self.player.start()
            self._autoplay_armed = True
        return pos

    def _play_next_track(self) -> None:
        if not self.items or self._current_index is None:
            return
        for nxt in range(self._current_index + 1, len(self.items)):
            if not self._is_playable_item(self.items[nxt]):
                continue
            self._autoplay_after_load = True
            self._load_pair(nxt)
            return

    def _clear_karaoke_playback(self) -> None:
        self._karaoke_entries = []
        self._karaoke_end_ts = []
        self._karaoke_pair = None
        self._karaoke_idx_hint = None
        self._karaoke_idx_hint_t = 0.0
        self._reset_karaoke_finish_state()
        self._reset_karaoke_loop()

    def _parse_karaoke_words(
        self,
        raw_words: object,
        *,
        line_start: float,
        line_end: float,
    ) -> List[KaraokeWord]:
        if not isinstance(raw_words, list):
            return []

        words: List[KaraokeWord] = []
        prev_end = line_start
        for item in raw_words:
            if not isinstance(item, dict):
                continue
            raw_word = item.get("word")
            if not isinstance(raw_word, str):
                continue
            word = raw_word.strip()
            if not word:
                continue

            start_raw = item.get("start_ts", prev_end)
            end_raw = item.get("end_ts", start_raw)
            try:
                start_ts = float(start_raw)
            except (TypeError, ValueError):
                start_ts = prev_end
            try:
                end_ts = float(end_raw)
            except (TypeError, ValueError):
                end_ts = start_ts

            if start_ts < prev_end:
                start_ts = prev_end
            if end_ts < start_ts:
                end_ts = start_ts

            if start_ts > line_end:
                start_ts = line_end
            if end_ts > line_end:
                end_ts = line_end

            words.append(
                {
                    "word": word,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                }
            )
            prev_end = end_ts

        return words

    def _load_karaoke_playback(self, pair: SongPair, show_errors: bool = True) -> bool:
        path = karaoke_path_for_pair(pair)
        if not path.exists():
            if show_errors:
                messagebox.showinfo("Karaoke not found", "No karaoke file found for this track.")
            self._clear_karaoke_playback()
            return False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            if show_errors:
                messagebox.showerror(
                    "Karaoke load failed",
                    f"Could not read karaoke file:\n{path}\n\n{exc}",
                )
            self._clear_karaoke_playback()
            return False
        if not isinstance(raw, list):
            if show_errors:
                messagebox.showerror(
                    "Karaoke load failed",
                    f"Karaoke file should be a list:\n{path}",
                )
            self._clear_karaoke_playback()
            return False

        raw_entries: List[Dict[str, object]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            line = item.get("line")
            end_ts = item.get("end_ts")
            if not isinstance(line, str):
                continue
            try:
                end_val = float(end_ts)
            except (TypeError, ValueError):
                continue
            raw_entries.append(
                {
                    "line": line,
                    "end_ts": end_val,
                    "start_ts": item.get("start_ts"),
                    "words": item.get("words"),
                }
            )

        if not raw_entries:
            if show_errors:
                messagebox.showerror(
                    "Karaoke load failed",
                    f"No valid entries found in:\n{path}",
                )
            self._clear_karaoke_playback()
            return False

        raw_entries.sort(key=lambda item: float(item["end_ts"]))
        entries: List[KaraokeEntry] = []
        prev_end = 0.0
        for item in raw_entries:
            end_val = float(item["end_ts"])
            if end_val < prev_end:
                end_val = prev_end

            start_raw = item.get("start_ts")
            if start_raw is None:
                start_val = prev_end
            else:
                try:
                    start_val = float(start_raw)
                except (TypeError, ValueError):
                    start_val = prev_end
            if start_val < prev_end:
                start_val = prev_end
            if start_val > end_val:
                start_val = end_val

            words = self._parse_karaoke_words(
                item.get("words"),
                line_start=start_val,
                line_end=end_val,
            )
            entries.append(
                {
                    "line": str(item["line"]),
                    "start_ts": start_val,
                    "end_ts": end_val,
                    "words": words,
                }
            )
            prev_end = end_val

        self._karaoke_entries = entries
        self._karaoke_end_ts = [item["end_ts"] for item in entries]
        self._karaoke_pair = pair
        self._karaoke_idx_hint = None
        self._karaoke_idx_hint_t = 0.0
        return True

    def _open_fullscreen(self, mode: str) -> None:
        if self.karaoke.is_open():
            self.karaoke.set_mode(mode)
        else:
            self.karaoke.open(mode)
        self.karaoke.set_song_title(self._current_pair.key if self._current_pair is not None else "")
        self.karaoke.set_countdown_enabled(self._karaoke_countdown_enabled)
        self.karaoke.set_finish_celebration_enabled(self._karaoke_finish_celebration_enabled)
        self._set_main_window_active(False)
        self.karaoke.set_play_controls_enabled(str(self.btn_play.cget("state")) != "disabled")
        self.karaoke.set_record_controls_enabled(self._recording_active)
        self._update_karaoke_ui()

    def _exit_fullscreen(self) -> None:
        self._cancel_karaoke_countdown()
        if self._recording_active:
            self._finish_recording(save=False)
            self.player.pause()
            self.player.stop()
        else:
            self.player.pause()
        self._reset_karaoke_finish_state()
        self.karaoke.close()
        self._set_main_window_active(True)

    def _set_main_window_active(self, active: bool) -> None:
        if active:
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
            except tk.TclError:
                pass
            return
        try:
            self.withdraw()
        except tk.TclError:
            pass

    def _recording_display_lines(self) -> tuple[List[str], int]:
        count = max(1, self._karaoke_visible_lines)
        if not self._recording_active:
            return [""] * count, 0
        idx = self._recording_index
        active_slot = idx % count
        slot_lines = [""] * count
        total = len(self._recording_lines)
        for slot_idx in range(count):
            line_idx = idx + ((slot_idx - active_slot) % count)
            if line_idx < total:
                slot_lines[slot_idx] = self._recording_lines[line_idx]
        return slot_lines, active_slot

    def _karaoke_display_state(
        self,
    ) -> tuple[List[str], int, List[str] | None, int, int | None, float]:
        count = max(1, self._karaoke_visible_lines)
        if not self._karaoke_entries:
            self._karaoke_idx_hint = None
            self._karaoke_idx_hint_t = 0.0
            return [""] * count, 0, None, 0, None, 0.0
        if self._karaoke_pair is not None and self._current_pair is not None:
            if self._karaoke_pair != self._current_pair:
                self._karaoke_idx_hint = None
                self._karaoke_idx_hint_t = 0.0
                return [""] * count, 0, None, 0, None, 0.0
        t = self.player.position_seconds()
        idx = bisect_right(self._karaoke_end_ts, t)
        if self._karaoke_idx_hint is not None and idx < self._karaoke_idx_hint:
            if t >= self._karaoke_idx_hint_t - 0.1 and not self.karaoke.is_seeking():
                idx = self._karaoke_idx_hint
        self._karaoke_idx_hint = idx
        self._karaoke_idx_hint_t = t
        if idx >= len(self._karaoke_entries):
            return [""] * count, idx % count, None, 0, None, 0.0

        active_slot = idx % count
        slot_lines = [""] * count
        total = len(self._karaoke_entries)
        for slot_idx in range(count):
            line_idx = idx + ((slot_idx - active_slot) % count)
            if line_idx < total:
                slot_lines[slot_idx] = self._karaoke_entries[line_idx]["line"]

        entry = self._karaoke_entries[idx]
        words = entry["words"]
        if not words:
            return slot_lines, active_slot, None, 0, None, 0.0

        word_end_ts = [word["end_ts"] for word in words]
        sung_words = bisect_right(word_end_ts, t)
        active_word_idx: int | None = None
        for word_idx, word in enumerate(words):
            if word["start_ts"] <= t < word["end_ts"]:
                active_word_idx = word_idx
                break

        if (
            active_word_idx is None
            and sung_words < len(words)
            and t >= words[sung_words]["start_ts"]
        ):
            active_word_idx = sung_words

        active_word_progress = 0.0
        if active_word_idx is not None:
            word = words[active_word_idx]
            word_start = word["start_ts"]
            word_end = word["end_ts"]
            if word_end <= word_start:
                active_word_progress = 1.0 if t >= word_end else 0.0
            else:
                active_word_progress = min(max((t - word_start) / (word_end - word_start), 0.0), 1.0)

        return (
            slot_lines,
            active_slot,
            [word["word"] for word in words],
            sung_words,
            active_word_idx,
            active_word_progress,
        )

    def _update_karaoke_ui(self) -> None:
        if not self.karaoke.is_open():
            return
        self.karaoke.set_song_title(self._current_pair.key if self._current_pair is not None else "")
        dur = self.player.duration_seconds()
        pos = self.player.position_seconds()
        playing = self.player.playing and not self.player.is_fading_out()
        self.karaoke.update_playback(pos, dur, playing, self.karaoke.is_seeking())
        self.karaoke.update_volume(
            self.player.mix.vocals_gain,
            self.player.mix.instr_gain,
            self.player.mix.vocals_muted,
            self.player.mix.instr_muted,
        )
        self.karaoke.update_loop_state(
            self._karaoke_loop_in,
            self._karaoke_loop_out,
            self._karaoke_loop_enabled,
            self._karaoke_loop_status_text(),
        )
        if self._recording_active:
            slot_lines, active_slot = self._recording_display_lines()
            self.karaoke.update_lines(slot_lines, active_slot)
        elif self._karaoke_countdown_value is not None:
            countdown = f"{self._karaoke_countdown_value}.."
            count = max(1, self._karaoke_visible_lines)
            active_slot = min(count - 1, count // 2)
            slot_lines = [""] * count
            slot_lines[active_slot] = countdown
            self.karaoke.update_lines(slot_lines, active_slot)
        elif self._karaoke_finish_message is not None and not playing:
            count = max(1, self._karaoke_visible_lines)
            active_slot = min(count - 1, count // 2)
            slot_lines = [""] * count
            slot_lines[active_slot] = self._karaoke_finish_message
            self.karaoke.update_lines(slot_lines, active_slot)
        else:
            slot_lines, active_slot, words, sung_words, active_word_idx, active_word_progress = (
                self._karaoke_display_state()
            )
            if words:
                self.karaoke.update_lines_with_words(
                    slot_lines,
                    active_slot,
                    words,
                    sung_words,
                    active_word_idx,
                    active_word_progress,
                )
            else:
                self.karaoke.update_lines(slot_lines, active_slot)

    def _on_close(self) -> None:
        self._dismiss_track_context_menu()
        self._cancel_karaoke_countdown()
        self._close_transpose_dialog()
        if self._transpose_running:
            messagebox.showinfo(
                "Transposition in progress",
                "Wait until the current transposition finishes before closing the app.",
            )
            return
        self._close_transpose_progress_window()
        if self._save_mp3_running:
            messagebox.showinfo(
                "MP3 export in progress",
                "Wait until the current MP3 export finishes before closing the app.",
            )
            return
        self._close_save_mp3_progress_window()
        self._close_process_settings_window()
        if not self._confirm_or_kill_running_process():
            return
        self._stop_music_processing()
        self._close_process_log_window()
        if self._ui_update_job is not None:
            try:
                self.after_cancel(self._ui_update_job)
            except tk.TclError:
                pass
            self._ui_update_job = None
        if self.karaoke.is_open():
            self.karaoke.close()
        self.player.close()
        self.destroy()

    def _start_karaoke_mode(self) -> None:
        if self._recording_active:
            return
        pair = self._current_pair
        if pair is None:
            messagebox.showinfo("No track selected", "Select a track to start karaoke mode.")
            return
        if self._loading:
            return
        if not self._load_karaoke_playback(pair, show_errors=True):
            return
        self._cancel_karaoke_countdown()
        self._open_fullscreen(mode="play")
        self._prepare_karaoke_start()
        if self._karaoke_countdown_enabled:
            self._run_karaoke_countdown(3)
        else:
            self._run_karaoke_countdown(0)
        self._update_karaoke_ui()

    def _prepare_karaoke_start(self) -> None:
        self._reset_karaoke_finish_state()
        if self.player.playing or self.player.is_fading_out():
            self.player.pause()
        self.player.stop()
        self._seek_karaoke_position(self._karaoke_loop_start_position(), reset_finish=False)
        self.player.set_vocals_gain(1.0, smooth=False)
        self.player.set_instr_gain(1.0, smooth=False)
        self.player.set_vocals_muted(True, smooth=False)
        self.player.set_instr_muted(False, smooth=False)
        self._sync_main_mix_controls()

    def _run_karaoke_countdown(self, value: int) -> None:
        if not self.karaoke.is_open():
            self._cancel_karaoke_countdown()
            return
        if value <= 0:
            self._karaoke_countdown_value = None
            self._karaoke_countdown_job = None
            self._seek_karaoke_position(self._karaoke_loop_start_position(), reset_finish=False)
            self.player.start()
            self._autoplay_armed = True
            self._add_current_track_to_history()
            self._update_karaoke_ui()
            return
        self._karaoke_countdown_value = value
        self._update_karaoke_ui()
        self._karaoke_countdown_job = self.after(
            1000,
            lambda: self._run_karaoke_countdown(value - 1),
        )

    def _cancel_karaoke_countdown(self) -> None:
        if self._karaoke_countdown_job is not None:
            try:
                self.after_cancel(self._karaoke_countdown_job)
            except tk.TclError:
                pass
            self._karaoke_countdown_job = None
        self._karaoke_countdown_value = None

    def _clean_lyrics_lines(self, raw: str) -> List[str]:
        lines: List[str] = []
        for line in raw.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if cleaned.startswith("["):
                continue
            lines.append(cleaned)
        return lines

    def _prompt_lyrics_text(
        self,
        title: str,
        action_label: str,
        no_track_message: str,
        on_lines: Callable[[List[str]], None],
    ) -> None:
        if self._recording_active:
            return
        if self._current_pair is None:
            messagebox.showinfo("No track selected", no_track_message)
            return

        if self._recording_window is not None and self._recording_window.winfo_exists():
            self._recording_window.lift()
            return

        win = tk.Toplevel(self)
        self._recording_window = win
        win.title(title)
        win.geometry("520x420")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text="Paste lyrics text below. Empty lines and lines starting with [ are ignored.",
            style="Subtle.TLabel",
            wraplength=480,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        text = tk.Text(win, wrap="word", height=14)
        text.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        ttk.Button(btn_row, text="Find lyrics", style="Ghost.TButton", command=self._open_genius).pack(
            side="left"
        )

        def on_cancel() -> None:
            win.grab_release()
            win.destroy()
            self._recording_window = None

        def on_start() -> None:
            raw = text.get("1.0", "end")
            lines = self._clean_lyrics_lines(raw)
            if not lines:
                messagebox.showerror("No lyrics", "Paste at least one non-empty lyric line.")
                return
            win.grab_release()
            win.destroy()
            self._recording_window = None
            on_lines(lines)

        ttk.Button(btn_row, text="Cancel", command=on_cancel).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text=action_label, style="Accent.TButton", command=on_start).pack(
            side="right"
        )

    def _prompt_recording_text(self) -> None:
        self._prompt_lyrics_text(
            title="Paste lyrics",
            action_label="Start recording",
            no_track_message="Select a track to start recording.",
            on_lines=self._start_recording,
        )

    def _start_recording(self, lines: List[str]) -> None:
        pair = self._current_pair
        if pair is None:
            return
        if self._loading:
            return
        self._reset_karaoke_finish_state()
        self._cancel_karaoke_countdown()
        if self.player.playing or self.player.is_fading_out():
            self.player.pause()
        self.player.stop()
        self.player.seek_seconds(0.0)
        self.seek.set(0.0)
        self._clear_karaoke_playback()
        self._recording_active = True
        self._recording_lines = lines
        self._recording_index = 0
        self._recording_karaoke = []
        self._recording_pair = pair
        self._autoplay_armed = False
        self._set_recording_ui_state(True)
        self._update_recording_status()
        self._open_fullscreen(mode="record")
        self.player.start()

    def _record_ts(self, t: float) -> float:
        if not self._recording_karaoke:
            return t
        last = self._recording_karaoke[-1]["end_ts"]
        if t <= last:
            return last + 0.001
        return t

    def _recording_progress_label(self) -> str:
        total = len(self._recording_lines)
        if total <= 0:
            return ""
        width = len(str(total))
        current = min(self._recording_index + 1, total)
        return f"{current:0{width}d}/{total:0{width}d}"

    def _append_remaining_recording_lines(self, end_ts: float) -> None:
        if not self._recording_active:
            return
        total = len(self._recording_lines)
        if self._recording_index >= total:
            return
        for idx in range(self._recording_index, total):
            t = self._record_ts(end_ts)
            self._recording_karaoke.append({"line": self._recording_lines[idx], "end_ts": t})
        self._recording_index = total

    def _record_next_line(self) -> None:
        if not self._recording_active:
            return
        if self._recording_index >= len(self._recording_lines):
            self._finish_recording(save=True)
            return
        t = self._record_ts(self.player.position_seconds())
        line = self._recording_lines[self._recording_index]
        self._recording_karaoke.append({"line": line, "end_ts": t})
        self._recording_index += 1
        if self._recording_index >= len(self._recording_lines):
            self._finish_recording(save=True)
            return
        self._update_recording_status()

    def _record_instrumental_end(self) -> None:
        if not self._recording_active:
            return
        t = self._record_ts(self.player.position_seconds())
        if self._recording_karaoke and self._recording_karaoke[-1]["line"] == "[Проигрыш]":
            self._recording_karaoke[-1]["end_ts"] = t
        else:
            self._recording_karaoke.append({"line": "[Проигрыш]", "end_ts": t})
        self._update_recording_status()

    def _update_recording_status(self) -> None:
        if not self._recording_active:
            msg = self._recording_done_message or ""
            if self.karaoke.is_open():
                self.karaoke.update_recording_status(msg)
            return
        msg = self._recording_progress_label()
        if self.karaoke.is_open():
            self.karaoke.update_recording_status(msg)

    def _set_recording_done_message(self, msg: str, ttl_ms: int = 3000) -> None:
        self._recording_done_message = msg
        if self._recording_done_job:
            try:
                self.after_cancel(self._recording_done_job)
            except tk.TclError:
                pass
        self._recording_done_job = self.after(ttl_ms, self._clear_recording_done_message)
        self._update_recording_status()

    def _clear_recording_done_message(self) -> None:
        self._recording_done_message = None
        self._recording_done_job = None
        self._update_recording_status()

    def _finish_recording(self, save: bool) -> None:
        if not self._recording_active:
            return
        pair = self._recording_pair
        data = list(self._recording_karaoke)
        self._recording_active = False
        self._recording_lines = []
        self._recording_index = 0
        self._recording_karaoke = []
        self._recording_pair = None
        self._set_recording_ui_state(False)
        self._update_recording_status()
        self._update_karaoke_ui()
        if not save or pair is None:
            return
        path = karaoke_path_for_pair(pair)
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Save failed", f"Could not save karaoke file:\n{path}\n\n{exc}")
            return
        self._apply_list_colors()
        self._update_karaoke_button_visibility()
        self._set_recording_done_message("Recording saved.")
