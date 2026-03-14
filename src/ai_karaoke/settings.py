from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .constants import CONFIG_PATH


@dataclass
class AppSettings:
    library_path: str = ""
    karaoke_font_size: int = 36
    karaoke_visible_lines: int = 3
    karaoke_countdown_enabled: bool = True
    karaoke_finish_celebration_enabled: bool = True
    process_jobs: int = 1
    process_genius_delay_seconds: float = 30.0
    process_only_align: bool = False

    @classmethod
    def load(cls) -> AppSettings:
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: dict[str, object]) -> AppSettings:
        settings = cls()
        for field in fields(cls):
            value = raw.get(field.name)
            if value is None:
                continue
            current = getattr(settings, field.name)
            if isinstance(current, bool):
                setattr(settings, field.name, _parse_bool(value, getattr(settings, field.name)))
                continue
            if isinstance(current, int) and not isinstance(current, bool):
                setattr(settings, field.name, _parse_int(value, getattr(settings, field.name)))
                continue
            if isinstance(current, float):
                setattr(settings, field.name, _parse_float(value, getattr(settings, field.name)))
                continue
            setattr(settings, field.name, str(value))
        settings.karaoke_font_size = max(20, min(72, settings.karaoke_font_size))
        settings.karaoke_visible_lines = max(1, min(7, settings.karaoke_visible_lines))
        settings.process_jobs = max(1, min(64, settings.process_jobs))
        settings.process_genius_delay_seconds = max(0.0, settings.process_genius_delay_seconds)
        return settings

    def to_mapping(self) -> dict[str, str]:
        return {
            "library_path": self.library_path,
            "karaoke_font_size": str(self.karaoke_font_size),
            "karaoke_visible_lines": str(self.karaoke_visible_lines),
            "karaoke_countdown_enabled": "1" if self.karaoke_countdown_enabled else "0",
            "karaoke_finish_celebration_enabled": (
                "1" if self.karaoke_finish_celebration_enabled else "0"
            ),
            "process_jobs": str(self.process_jobs),
            "process_genius_delay_seconds": str(self.process_genius_delay_seconds),
            "process_only_align": "1" if self.process_only_align else "0",
        }

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(self.to_mapping(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def default_library_path() -> Path:
    return (Path.home() / "Music" / "stems_library").resolve()


def resolve_library_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _parse_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
