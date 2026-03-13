from __future__ import annotations

import os
from pathlib import Path

VOCALS_TAG = "_(Vocals)"
INSTR_TAG = "_(Instrumental)"
GENIUS_TAG = "_(Genius Lyrics)"
KARAOKE_TAG = "_(Karaoke Lyrics)"
PLAYLISTS_FILE = ".ai_karaoke_playlists.json"

DEFAULT_SR = 44100
DEFAULT_CH = 2


def _resolve_config_path() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "ai_karaoke" / "config.json"
    return Path.home() / ".config" / "ai_karaoke.json"


CONFIG_PATH = _resolve_config_path()
