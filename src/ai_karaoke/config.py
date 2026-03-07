from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .constants import CONFIG_PATH


def load_config() -> Dict[str, str]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict):
        return {k: str(v) for k, v in data.items()}
    return {}


def save_config(config: Dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def default_library_path() -> Path:
    return (Path.home() / "Music" / "stems_library").resolve()


def resolve_library_path(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()
