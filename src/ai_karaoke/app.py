from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import default_library_path, load_config, resolve_library_path, save_config
from .ui_main import App


def main() -> None:
    config = load_config()
    raw_path = config.get("library_path")
    invalid_path: Optional[str] = None
    if raw_path:
        candidate = resolve_library_path(raw_path)
    else:
        candidate = default_library_path()

    if candidate.exists() and not candidate.is_dir():
        invalid_path = raw_path or str(candidate)
        candidate = default_library_path()

    try:
        candidate.mkdir(parents=True, exist_ok=True)
        folder = candidate
    except OSError:
        if raw_path:
            invalid_path = raw_path
        folder = Path.cwd().resolve()

    config["library_path"] = str(folder)
    save_config(config)

    app = App(folder, invalid_path=invalid_path, config=config)
    # App may have destroyed itself on error.
    if app.winfo_exists():
        app.mainloop()
