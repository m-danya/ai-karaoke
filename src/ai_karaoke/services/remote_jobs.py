"""Explicit processing/export job resources, independent of desktop controllers."""
from __future__ import annotations

import copy
import math
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..controllers.process_runner import MusicProcessRunner, music_processing_python
from ..library_paths import karaoke_path_for_pair, genius_lyrics_path_for_pair
from ..models import ExportMixSettings, KaraokeRenderSettings, VideoExportSettings
from .remote_server import ApiError


def number(body, key, default, low, high, integer=False):
    value = float(body.get(key, default))
    if not math.isfinite(value) or not low <= value <= high or (integer and value != int(value)):
        raise ApiError(f"{key} must be between {low} and {high}")
    return int(value) if integer else value


class RemoteJobs:
    def __init__(self, library):
        self.library = library
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="karaoke-job")
        self.records = {}
        self.runners = {}
        self.cancelled = set()
        self.temp = tempfile.TemporaryDirectory(prefix="ai-karaoke-exports-")
        self.closed = False

    def submit(self, body):
        operation = body.get("operation")
        if operation not in {"transpose", "export_mp3", "export_mp4", "process"}:
            raise ApiError("Unknown operation")
        pair = self.library.pair(body.get("track_id")) if operation != "process" else None
        if pair:
            self.library.safe_path(karaoke_path_for_pair(pair))
            self.library.safe_path(genius_lyrics_path_for_pair(pair))
        options = {}
        if operation == "transpose":
            options["semitones"] = number(body, "semitones", 0, -12, 12, True)
            if not options["semitones"]:
                raise ApiError("Choose a non-zero transposition")
        elif operation == "export_mp3":
            options["mix"] = ExportMixSettings("Mobile", number(body, "vocals_gain", 1, 0, 2),
                                               number(body, "instrumental_gain", 1, 0, 2),
                                               bool(body.get("vocals_muted", False)), bool(body.get("instrumental_muted", False)))
        elif operation == "export_mp4":
            options["video"] = VideoExportSettings("Mobile", "custom", number(body, "width", 1280, 320, 3840, True),
                                                   number(body, "height", 720, 240, 2160, True), number(body, "fps", 30, 1, 60, True))
            if options["video"].width % 2 or options["video"].height % 2:
                raise ApiError("Video dimensions must be even")
            options["render"] = KaraokeRenderSettings(number(body, "font_size", 36, 12, 96, True),
                                                       number(body, "visible_lines", 3, 1, 8, True),
                                                       bool(body.get("countdown", True)), bool(body.get("celebration", True)))
        else:
            options["path"] = self.library.safe_path(self.library.folder / str(body.get("path", "")))
            if not options["path"].exists():
                raise ApiError("Processing path does not exist", 404)
            options["workers"] = number(body, "workers", 1, 1, 16, True)
            options["delay"] = number(body, "genius_delay", 3, 0, 300)
            options["only_align"] = bool(body.get("only_align", False))
        with self.lock:
            if self.closed:
                raise ApiError("Server is stopping", 503)
            if sum(r["status"] in {"queued", "running"} for r in self.records.values()) >= 4:
                raise ApiError("Job queue is full. Wait for an existing job to finish.", 429)
            # Job resources remain bounded; clients keep their own list of job IDs.
            finished = [key for key, r in self.records.items() if r["status"] not in {"queued", "running"}]
            for key in finished[:-24]:
                record = self.records.pop(key)
                if record.get("file"):
                    Path(record["file"]).unlink(missing_ok=True)
            job_id = uuid.uuid4().hex
            self.records[job_id] = {"id": job_id, "operation": operation, "status": "queued", "log": "", "result": None}
            self.pool.submit(self._run, job_id, operation, pair, options)
            return self.get(job_id)

    def get(self, job_id):
        with self.lock:
            record = self.records.get(job_id)
            if record is None:
                raise ApiError("Job expired or server restarted", 404)
            return copy.deepcopy({k: v for k, v in record.items() if k != "file"})

    def update(self, job_id, **values):
        with self.lock:
            self.records[job_id].update(values)

    def log(self, job_id, text):
        with self.lock:
            self.records[job_id]["log"] = (self.records[job_id]["log"] + text + "\n")[-32000:]

    def download(self, job_id):
        with self.lock:
            record = self.records.get(job_id, {})
            if record.get("status") != "done" or not record.get("file"):
                raise ApiError("Download is not ready", 404)
            return Path(record["file"])

    def cancel(self, job_id):
        with self.lock:
            record = self.get(job_id)
            if record["status"] in {"queued", "running"}:
                if record["operation"] != "process":
                    raise ApiError("This export/transposition must finish before it can be removed", 409)
                self.cancelled.add(job_id)
            return record

    def _run(self, job_id, operation, pair, options):
        mutating = operation in {"transpose", "process"}
        locked = False
        try:
            if mutating:
                locked = self.library.mutation_lock.acquire(blocking=False)
                if not locked:
                    raise ApiError("Another library operation is running; retry after it finishes", 409)
            with self.lock:
                if self.closed or job_id in self.cancelled:
                    self.update(job_id, status="cancelled")
                    return
                self.update(job_id, status="running")
            result = None
            if operation == "process":
                self._process(job_id, options)
            elif operation == "transpose":
                from .transpose_service import transpose_track_copy
                paths = transpose_track_copy(pair, options["semitones"])
                self.library.catalog()
                result = {"track_id": self.library.track_id(paths.vocals)}
            else:
                suffix = ".mp3" if operation == "export_mp3" else ".mp4"
                output = Path(self.temp.name) / (job_id + suffix)
                if operation == "export_mp3":
                    from .export_service import render_mix_to_mp3
                    render_mix_to_mp3(pair, output, options["mix"], sr=44100)
                else:
                    from .karaoke_file_service import load_karaoke_entries_for_pair
                    from .video_export_service import render_karaoke_to_mp4
                    self.library.safe_path(karaoke_path_for_pair(pair))
                    render_karaoke_to_mp4(pair, output, load_karaoke_entries_for_pair(pair), title=pair.key,
                        colors={"bg": "#f4f1ec", "text": "#1f2328", "muted": "#6b645c", "accent": "#0d6b5f", "karaoke": "#1b7f2a"},
                        render_settings=options["render"], video_settings=options["video"], finish_message="Bravo!",
                        progress_callback=lambda text: self.log(job_id, text), sr=44100)
                self.update(job_id, file=str(output))
                result = {"download_url": f"/api/download/{job_id}", "filename": pair.key + suffix}
            self.update(job_id, status="cancelled" if job_id in self.cancelled else "done", result=result)
        except Exception as exc:
            self.update(job_id, status="cancelled" if job_id in self.cancelled else "error", error=str(exc))
        finally:
            if locked:
                self.library.mutation_lock.release()

    def _process(self, job_id, options):
        runner = MusicProcessRunner()
        with self.lock:
            self.runners[job_id] = runner
        project_root = Path(__file__).resolve().parents[3]
        command = [music_processing_python(project_root), "-u", "-m", "ai_karaoke.music_processing.main", str(options["path"]),
                   "--jobs", str(options["workers"]), "--genius-delay-seconds", str(options["delay"])]
        if options["only_align"]:
            command.append("--only-align")
        try:
            runner.start(command, cwd=project_root)
            while True:
                if self.closed or job_id in self.cancelled:
                    runner.stop(force_kill=True)
                    self.cancelled.add(job_id)
                    return
                lines, code = runner.poll()
                for line in lines:
                    self.log(job_id, line.rstrip())
                if code is not None:
                    if code:
                        raise RuntimeError(f"Processing exited with code {code}. See the log.")
                    self.library.catalog()
                    return
                time.sleep(0.1)
        finally:
            runner.stop()
            with self.lock:
                self.runners.pop(job_id, None)

    def close(self):
        with self.lock:
            self.closed = True
        # Wait/cleanup outside Tk; active encoders finish and processing kills its own process group.
        def cleanup():
            self.pool.shutdown(wait=True)
            self.temp.cleanup()
        threading.Thread(target=cleanup, daemon=True, name="karaoke-job-cleanup").start()
