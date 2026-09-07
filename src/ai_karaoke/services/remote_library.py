"""Library resources for mobile clients. Never stores a client's playback state."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from ..constants import INSTR_TAG, VOCALS_TAG
from ..library_paths import genius_lyrics_path_for_pair, karaoke_path_for_pair, storage_track_id
from ..library_scan import scan_folder
from ..models import SongPair
from ..playlist_store import load_playlists
from .karaoke_file_service import load_karaoke_entries_for_pair
from .remote_server import ApiError


class RemoteLibrary:
    def __init__(self, folder: Path):
        from .remote_jobs import RemoteJobs
        self.folder = folder.resolve()
        self.identity = hashlib.sha256(str(self.folder).encode()).hexdigest()[:24]
        self.lock = threading.RLock()
        self.mutation_lock = threading.Lock()
        self.pairs: dict[str, SongPair] = {}
        self.jobs = RemoteJobs(self)
        self.catalog()

    def safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.folder):
            raise ApiError("Path is outside the shared library", 403)
        return resolved

    def track_id(self, path: Path | str) -> str:
        relative = storage_track_id(str(path), folder=self.folder)
        return hashlib.sha256(relative.encode()).hexdigest()[:24]

    def catalog(self):
        with self.lock:
            pairs = {}
            for pair in scan_folder(self.folder):
                try:
                    self.safe_path(pair.vocals)
                    self.safe_path(pair.instrumental)
                except ApiError:
                    continue
                pairs[self.track_id(pair.vocals)] = pair
            self.pairs = pairs
            playlists, history = load_playlists(self.folder)
            labels = {self.track_id(path): Path(path).name.replace(VOCALS_TAG, "").removesuffix(".mp3")
                      for paths in [history, *playlists.values()] for path in paths}
            tracks = [self.describe(key, pair) for key, pair in pairs.items()]
            tracks.extend({"id": key, "title": label, "missing": True, "karaoke": False, "folder": ""}
                          for key, label in labels.items() if key not in pairs)
            return {"library_id": self.identity, "name": self.folder.name,
                    "tracks": tracks, "playlists": {name: [self.track_id(p) for p in ids] for name, ids in playlists.items()},
                    "history": [self.track_id(p) for p in history],
                    "folders": sorted({"", *(str(p.vocals.parent.relative_to(self.folder)) for p in pairs.values())})}

    def describe(self, track_id, pair):
        karaoke = self.safe_path(karaoke_path_for_pair(pair))
        return {"id": track_id, "title": pair.key, "missing": False, "karaoke": karaoke.is_file(),
                "folder": str(pair.vocals.parent.relative_to(self.folder)),
                "vocals_url": f"/api/tracks/{track_id}/audio/vocals",
                "instrumental_url": f"/api/tracks/{track_id}/audio/instrumental"}

    def pair(self, track_id) -> SongPair:
        with self.lock:
            pair = self.pairs.get(track_id)
            if pair is None:
                self.catalog()
                pair = self.pairs.get(track_id)
            if pair is None:
                raise ApiError("Track not found. Refresh the library.", 404)
            for path in (pair.vocals, pair.instrumental):
                if not self.safe_path(path).is_file():
                    raise ApiError("Track files are missing. Refresh the library.", 404)
            return pair

    def track(self, track_id):
        pair = self.pair(track_id)
        result = self.describe(track_id, pair)
        result["lyrics"] = []
        result["lyrics_error"] = None
        if result["karaoke"]:
            try:
                result["lyrics"] = load_karaoke_entries_for_pair(pair)
            except (ValueError, OSError) as exc:
                result["lyrics_error"] = str(exc)
        text_path = self.safe_path(genius_lyrics_path_for_pair(pair))
        result["lyrics_text"] = text_path.read_text(encoding="utf-8") if text_path.is_file() else ""
        return result

    def audio(self, track_id, stem):
        pair = self.pair(track_id)
        if stem not in {"vocals", "instrumental"}:
            raise ApiError("Unknown stem", 404)
        return self.safe_path(getattr(pair, stem))

    def delete(self, track_id):
        if not self.mutation_lock.acquire(blocking=False):
            raise ApiError("Library processing is in progress", 409)
        try:
            pair = self.pair(track_id)
            paths = [self.safe_path(p) for p in (pair.vocals, pair.instrumental,
                     karaoke_path_for_pair(pair), genius_lyrics_path_for_pair(pair))]
            for path in paths:
                path.unlink(missing_ok=True)
            self.catalog()
            return {"deleted": track_id}
        finally:
            self.mutation_lock.release()

    def import_mp3(self, name, folder, stream, length):
        if not name or Path(name).name != name or "\\" in name or not name.lower().endswith(".mp3"):
            raise ApiError("Choose an MP3 file with a plain filename")
        if any(tag in name for tag in (INSTR_TAG, VOCALS_TAG)):
            raise ApiError("Import an original MP3, not a processed stem")
        if not 0 < length <= 500 * 1024 * 1024:
            raise ApiError("MP3 must be between 1 byte and 500 MB", 413)
        destination = self.safe_path(self.folder / folder / name)
        if not self.mutation_lock.acquire(blocking=False):
            raise ApiError("Library processing is in progress", 409)
        created = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                handle = destination.open("xb")
            except FileExistsError:
                raise ApiError("A file with this name already exists", 409)
            created = True
            with handle:
                while length:
                    data = stream.read(min(length, 256 * 1024))
                    if not data:
                        raise ApiError("Upload was interrupted")
                    handle.write(data)
                    length -= len(data)
            return {"file": str(destination.relative_to(self.folder))}
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise
        finally:
            self.mutation_lock.release()

    def submit(self, body):
        return self.jobs.submit(body)

    def close(self):
        self.jobs.close()
