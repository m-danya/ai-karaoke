"""Temporary generated library for mobile smoke checks. Never touches user music."""
import json
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from ai_karaoke.controllers.server_mode import ServerModeController

with tempfile.TemporaryDirectory(prefix='karaoke-smoke-') as directory:
    folder = Path(directory)
    for name, frequency in [('Первая песня', 440), ('Вторая песня', 660)]:
        for stem, hz in [('Vocals', frequency), ('Instrumental', frequency / 2)]:
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                            f'sine=frequency={hz}:duration=24:sample_rate=44100', '-ac', '2', '-q:a', '4',
                            str(folder / f'{name}_({stem}).mp3')], check=True)
        entries = []
        for i, line in enumerate(['Мы поём сегодня вместе', 'Слышишь музыку вокруг', 'Каждый голос в этой песне', 'Поддержи меня мой друг']):
            words = line.split()
            entries.append({'line': line, 'start_ts': i * 5 + 2, 'end_ts': i * 5 + 6,
                            'words': [{'word': word, 'start_ts': i * 5 + 2 + j, 'end_ts': i * 5 + 3 + j} for j, word in enumerate(words)]})
        (folder / f'{name}_(Karaoke Lyrics).json').write_text(json.dumps(entries, ensure_ascii=False))
    controller = ServerModeController()
    controller.start(folder)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print('Demo ready on 0.0.0.0:9595', flush=True)
    try:
        stop.wait()
    finally:
        controller.stop()
