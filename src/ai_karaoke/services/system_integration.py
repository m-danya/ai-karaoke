from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Iterable


def open_genius_search(label: str) -> None:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-я]+", " ", label).strip()
    if not cleaned:
        return
    url = f"https://www.google.com/search?q=genius+{urllib.parse.quote_plus(cleaned)}"
    webbrowser.open(url, new=2)


def run_external_command(cmd: Iterable[str]) -> bool:
    try:
        subprocess.run(
            list(cmd),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return False


def show_in_file_manager(path: Path) -> None:
    target = path.resolve()
    if os.name == "nt":
        if run_external_command(["explorer", "/select,", str(target)]):
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
            if run_external_command(cmd):
                return
        raise OSError("Could not launch a supported file manager.")

    raise OSError(f"Unsupported platform: {sys.platform}")
