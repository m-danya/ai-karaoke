from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
from pathlib import Path


def music_processing_python(project_root: Path) -> str:
    candidate = project_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(candidate) if candidate.is_file() else sys.executable


class MusicProcessRunner:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._output_queue: queue.Queue[tuple[str, object]] | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, command: list[str], *, cwd: Path) -> None:
        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
            "cwd": str(cwd),
        }
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        if process.stdout is None:
            process.terminate()
            raise RuntimeError("Failed to capture process output.")

        self._process = process
        self._output_queue = queue.Queue()

        def _reader() -> None:
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    output_queue = self._output_queue
                    if output_queue is None:
                        return
                    output_queue.put(("line", line))
            return_code = process.wait()
            output_queue = self._output_queue
            if output_queue is not None:
                output_queue.put(("done", return_code))

        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def poll(self) -> tuple[list[str], int | None]:
        output_queue = self._output_queue
        if output_queue is None:
            return [], None

        lines: list[str] = []
        return_code: int | None = None
        while True:
            try:
                event, payload = output_queue.get_nowait()
            except queue.Empty:
                break
            if event == "line":
                lines.append(str(payload))
                continue
            if event == "done":
                try:
                    return_code = int(payload)
                except (TypeError, ValueError):
                    return_code = 1
                break
        return lines, return_code

    def stop(self, *, force_kill: bool = False) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            self._terminate_process(process, force_kill=force_kill)
        self._process = None
        self._output_queue = None
        self._reader_thread = None

    def _terminate_process(self, process: subprocess.Popen[str], *, force_kill: bool) -> None:
        if process.poll() is not None:
            return

        if os.name != "nt":
            try:
                child_pgid = os.getpgid(process.pid)
            except (OSError, ProcessLookupError):
                child_pgid = None
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
