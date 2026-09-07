"""Desktop-owned server lifecycle, with an independent library and no player hooks."""
from pathlib import Path

from ..services.remote_library import RemoteLibrary
from ..services.remote_server import RemoteServer


class ServerModeController:
    def __init__(self):
        self.server: RemoteServer | None = None

    @property
    def running(self):
        return self.server is not None

    @property
    def library_name(self):
        return self.server.library.folder.name if self.server else ""

    def start(self, folder: Path):
        if self.running:
            return
        library = RemoteLibrary(folder)
        server = RemoteServer(library)
        try:
            server.start()
        except Exception:
            library.close()
            raise
        self.server = server

    def stop(self):
        if self.server is not None:
            self.server.stop()
            self.server.library.close()
            self.server = None
