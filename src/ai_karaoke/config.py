from __future__ import annotations

from typing import Dict

from .settings import AppSettings, default_library_path, resolve_library_path


def load_config() -> Dict[str, str]:
    return AppSettings.load().to_mapping()


def save_config(config: Dict[str, str]) -> None:
    AppSettings.from_mapping(config).save()
