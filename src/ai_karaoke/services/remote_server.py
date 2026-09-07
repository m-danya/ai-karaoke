"""Stateless LAN transport. HTTP threads never access Tk or desktop playback."""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

PORT = 9595
DISCOVERY_REQUEST = b"AI_KARAOKE_DISCOVER_V1"
WEB_ROOT = Path(__file__).resolve().parents[1] / "remote_web"
APP_ORIGINS = {"http://localhost", "https://localhost", "capacitor://localhost"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class RemoteServer:
    def __init__(self, library):
        self.library = library
        self.http = None
        self.udp = None
        self.threads = []
        self.stopping = threading.Event()

    @property
    def identity(self):
        return {"service": "ai-karaoke", "version": 1, "name": socket.gethostname(),
                "port": PORT, "library_id": self.library.identity}

    def start(self):
        if self.http is not None:
            return
        owner = self
        self.stopping.clear()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(30)

            def send_headers(self, status, content_type, length, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                origin = self.headers.get("Origin", "")
                if origin in APP_ORIGINS:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Content-Disposition")
                for key, value in (extra or {}).items():
                    self.send_header(key, str(value))
                self.end_headers()

            def respond(self, value, status=200):
                data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
                self.send_headers(status, "application/json; charset=utf-8", len(data))
                if self.command != "HEAD":
                    self.wfile.write(data)

            def do_OPTIONS(self):
                self.send_headers(204, "text/plain", 0, {
                    "Access-Control-Allow-Headers": "Content-Type, Range",
                    "Access-Control-Allow-Methods": "GET, HEAD, POST, DELETE, OPTIONS",
                })

            def do_GET(self):
                self.handle_request()

            do_POST = do_GET
            do_DELETE = do_GET
            do_HEAD = do_GET

            def send_file(self, file, attachment=False):
                with file.open("rb") as stream:
                    size = os.fstat(stream.fileno()).st_size
                    start, end, status = 0, size - 1, 200
                    extra = {"Accept-Ranges": "bytes"}
                    raw_range = self.headers.get("Range")
                    if raw_range:
                        try:
                            if not raw_range.startswith("bytes=") or "," in raw_range:
                                raise ValueError()
                            first, last = raw_range[6:].split("-", 1)
                            if first:
                                start = int(first)
                                end = min(int(last), size - 1) if last else size - 1
                            else:
                                suffix = int(last)
                                if suffix <= 0:
                                    raise ValueError()
                                start = max(0, size - suffix)
                            if start < 0 or start >= size or end < start:
                                raise ValueError()
                        except ValueError:
                            self.send_headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                            return
                        status = 206
                        extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                    if attachment:
                        extra["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(file.name)
                    remaining = max(0, end - start + 1)
                    self.send_headers(status, mimetypes.guess_type(file.name)[0] or "application/octet-stream", remaining, extra)
                    if self.command == "HEAD":
                        return
                    stream.seek(start)
                    while remaining:
                        chunk = stream.read(min(remaining, 256 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)

            def handle_request(self):
                try:
                    url = urlsplit(self.path)
                    path = unquote(url.path)
                    parts = path.strip("/").split("/")
                    read = self.command in {"GET", "HEAD"}
                    if not read:
                        origin = self.headers.get("Origin")
                        if origin and origin not in APP_ORIGINS | {"http://" + self.headers.get("Host", "")}:
                            raise ApiError("Origin is not allowed", 403)
                    if read and path == "/api/health":
                        self.respond(owner.identity)
                    elif read and path == "/api/library":
                        self.respond(owner.library.catalog())
                    elif read and len(parts) == 3 and parts[:2] == ["api", "tracks"]:
                        self.respond(owner.library.track(parts[2]))
                    elif read and len(parts) == 5 and parts[:2] == ["api", "tracks"] and parts[3] == "audio":
                        self.send_file(owner.library.audio(parts[2], parts[4]))
                    elif read and len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                        self.respond(owner.library.jobs.get(parts[2]))
                    elif read and len(parts) == 3 and parts[:2] == ["api", "download"]:
                        self.send_file(owner.library.jobs.download(parts[2]), True)
                    elif self.command == "DELETE" and len(parts) == 3 and parts[:2] == ["api", "tracks"]:
                        self.respond(owner.library.delete(parts[2]))
                    elif self.command == "DELETE" and len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                        self.respond(owner.library.jobs.cancel(parts[2]))
                    elif self.command == "POST" and path == "/api/import":
                        query = parse_qs(url.query)
                        self.respond(owner.library.import_mp3(query.get("name", [""])[0], query.get("folder", [""])[0],
                                                             self.rfile, int(self.headers.get("Content-Length", "0"))), 201)
                    elif self.command == "POST" and path == "/api/jobs":
                        if self.headers.get_content_type() != "application/json":
                            raise ApiError("Expected application/json", 415)
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 < length <= 1024 * 1024:
                            raise ApiError("Invalid request size", 413)
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict):
                            raise ApiError("Expected a JSON object")
                        self.respond(owner.library.submit(body), 202)
                    elif read and not path.startswith("/api/"):
                        file = (WEB_ROOT / (path.lstrip("/") or "index.html")).resolve()
                        if not file.is_relative_to(WEB_ROOT.resolve()) or not file.is_file():
                            raise ApiError("Web client not found. Run scripts/build-web.sh.", 404)
                        self.send_file(file)
                    else:
                        raise ApiError("Not found", 404)
                except ApiError as exc:
                    self.respond({"error": str(exc)}, exc.status)
                except FileNotFoundError:
                    self.respond({"error": "Resource no longer exists"}, 404)
                except (ValueError, TypeError, KeyError) as exc:
                    self.respond({"error": str(exc)}, 400)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except Exception as exc:
                    self.respond({"error": str(exc)}, 500)

        http = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        http.daemon_threads = True
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp.bind(("0.0.0.0", PORT))
            udp.settimeout(0.25)
        except Exception:
            udp.close()
            http.server_close()
            raise
        self.http, self.udp = http, udp

        def discover():
            while not self.stopping.is_set():
                try:
                    data, address = udp.recvfrom(128)
                    if data == DISCOVERY_REQUEST:
                        udp.sendto(json.dumps(self.identity).encode(), address)
                except socket.timeout:
                    continue
                except OSError:
                    break

        self.threads = [threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True),
                        threading.Thread(target=discover, daemon=True)]
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stopping.set()
        if self.udp:
            self.udp.close()
            self.udp = None
        if self.http:
            self.http.shutdown()
            self.http.server_close()
            self.http = None
        for thread in self.threads:
            thread.join(timeout=1)
        self.threads = []
