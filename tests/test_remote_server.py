"""LAN contract checks using real HTTP sockets and temporary library files."""
import concurrent.futures
import json
import socket
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ai_karaoke.controllers.server_mode import ServerModeController
from ai_karaoke.services.remote_server import DISCOVERY_REQUEST


class RemoteServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vocals = self.root / 'Тест_(Vocals).mp3'
        self.vocals.write_bytes(bytes(range(256)) * 16)
        (self.root / 'Тест_(Instrumental).mp3').write_bytes(b'instrumental')
        (self.root / 'Тест_(Karaoke Lyrics).json').write_text(json.dumps([
            {'line': 'Hello', 'end_ts': 2},
            {'line': 'world', 'start_ts': 2, 'end_ts': 4, 'words': [{'word': 'world', 'start_ts': 2, 'end_ts': 4}]},
        ]))
        self.controller = ServerModeController()
        self.controller.start(self.root)
        self.track_id = self.request('/api/library')[1]['tracks'][0]['id']

    def tearDown(self):
        self.controller.stop()
        self.temp.cleanup()

    def request(self, path, method='GET', data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        request = Request('http://127.0.0.1:9595' + path, data=body, method=method,
                          headers={'Content-Type': 'application/json', **(headers or {})})
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_discovery_and_restart(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(2)
            udp.sendto(DISCOVERY_REQUEST, ('127.0.0.1', 9595))
            identity = json.loads(udp.recv(2048))
            self.assertEqual(identity['service'], 'ai-karaoke')
            self.assertEqual(identity['port'], 9595)
        self.assertEqual(self.controller.server.http.server_address[0], '0.0.0.0')
        self.controller.stop()
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', 9595), timeout=0.3)
        self.controller.start(self.root)
        self.assertEqual(self.request('/api/health')[1]['library_id'], identity['library_id'])

    def test_parallel_clients_have_no_playback_session(self):
        paths = ['/api/library', f'/api/tracks/{self.track_id}'] * 8
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.request, paths))
        self.assertTrue(all(status == 200 for status, _ in results))
        detail = results[1][1]
        self.assertEqual(detail['lyrics'][0]['start_ts'], 0)
        for forbidden in ('player', 'position', 'playing', 'volume', 'session'):
            self.assertNotIn(forbidden, detail)
        with self.assertRaises(HTTPError) as context:
            self.request('/api/state')
        self.assertEqual(context.exception.code, 404)

    def test_range_head_and_cors(self):
        url = f'http://127.0.0.1:9595/api/tracks/{self.track_id}/audio/vocals'
        for byte_range, expected in [('bytes=12-39', self.vocals.read_bytes()[12:40]),
                                     ('bytes=-19', self.vocals.read_bytes()[-19:]),
                                     ('bytes=4090-', self.vocals.read_bytes()[4090:])]:
            with urlopen(Request(url, headers={'Range': byte_range, 'Origin': 'http://localhost'})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'http://localhost')
        with urlopen(Request(url, method='HEAD')) as response:
            self.assertEqual(response.headers['Content-Length'], str(self.vocals.stat().st_size))
            self.assertEqual(response.read(), b'')
        with self.assertRaises(HTTPError) as context:
            urlopen(Request(url, headers={'Range': 'bytes=9000-'}))
        self.assertEqual(context.exception.code, 416)

    def test_containment_and_foreign_origin(self):
        with self.assertRaises(HTTPError) as context:
            self.request('/api/jobs', 'POST', {'operation': 'process', 'path': '..'})
        self.assertEqual(context.exception.code, 403)
        with self.assertRaises(HTTPError) as context:
            self.request(f'/api/tracks/{self.track_id}', 'DELETE', headers={'Origin': 'https://unrelated.example'})
        self.assertEqual(context.exception.code, 403)
        self.assertTrue(self.vocals.exists())
        outside = self.root.parent / ('outside-' + self.root.name + '.mp3')
        try:
            outside.write_bytes(b'private')
            (self.root / 'Escape_(Vocals).mp3').symlink_to(outside)
            (self.root / 'Escape_(Instrumental).mp3').symlink_to(outside)
            self.assertEqual(len(self.request('/api/library')[1]['tracks']), 1)
        finally:
            outside.unlink(missing_ok=True)

    def test_delete_preserves_missing_playlist_entry(self):
        (self.root / '.ai_karaoke_playlists.json').write_text(json.dumps({'playlists': {'Party': [self.vocals.name]}, 'history': []}))
        self.request(f'/api/tracks/{self.track_id}', 'DELETE')
        data = self.request('/api/library')[1]
        self.assertEqual(data['playlists']['Party'], [self.track_id])
        self.assertTrue(data['tracks'][0]['missing'])
        self.assertFalse(self.vocals.exists())

    def test_job_validation_does_not_start_invalid_operations(self):
        for body in [{'operation': 'play'}, {'operation': 'transpose', 'track_id': self.track_id, 'semitones': 0},
                     {'operation': 'export_mp3', 'track_id': self.track_id, 'vocals_gain': float('nan')},
                     {'operation': 'export_mp4', 'track_id': self.track_id, 'width': 321}]:
            with self.assertRaises(HTTPError) as context:
                self.request('/api/jobs', 'POST', body)
            self.assertEqual(context.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
