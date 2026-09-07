# LAN API v1

Base URL: `http://HOST:9595`. No authentication, cookies or client sessions.
Responses and errors are JSON (`{"error":"..."}`), except audio/downloads/uploads.
The server is enabled and disabled by the desktop Server mode button.

## Resource endpoints

| Method and path | Result / input |
| --- | --- |
| `GET /api/health` | `service: "ai-karaoke"`, `version: 1`, server `name`, `port: 9595`, `library_id` |
| `GET /api/library` | Current recursive catalog: `tracks`, `folders`, initial `playlists`, desktop `history`, `library_id`, `name` |
| `GET /api/tracks/{id}` | Track metadata, normalized `lyrics`, `lyrics_text`, optional `lyrics_error`, explicit stem URLs |
| `GET /api/tracks/{id}/audio/vocals` | Original vocal MP3 |
| `GET /api/tracks/{id}/audio/instrumental` | Original instrumental MP3 |
| `DELETE /api/tracks/{id}` | Remove both stems and related Genius/karaoke files; preserve playlist references |
| `POST /api/import?name=Song.mp3&folder=Artist/Album` | Raw MP3 body with Content-Length (max 500 MB), no overwrite; path relative to shared library |
| `POST /api/jobs` | JSON job parameters; returns a job resource, HTTP 202 |
| `GET /api/jobs/{id}` | `id`, `operation`, `status`, `log`, `result`, optional `error` |
| `DELETE /api/jobs/{id}` | Cancel a queued/running processing job, including its process group |
| `GET /api/download/{job_id}` | Completed export attachment |

Audio and downloads support HEAD and single HTTP byte ranges (206/416). Android
origins `http://localhost`, `https://localhost`, `capacitor://localhost` receive
CORS headers. Write requests reject unrelated browser origins. Files outside the
shared root are not exposed, including externally linked stems.

Track IDs are opaque hashes of library-relative paths. They are stable across
rescans and do not contain an absolute server path. A client must refresh after
library mutations. Missing desktop playlist references have `missing: true`,
`karaoke: false` and no playable audio URLs.

## Job requests

All parameters are explicit; no values are read from the desktop player or another
client. `Content-Type: application/json` is required.

```json
{"operation":"transpose","track_id":"ID","semitones":2}
```

Semitones: nonzero integer from −12 to +12. Creates a new paired track and copies
related lyrics; result contains its `track_id`.

```json
{"operation":"export_mp3","track_id":"ID","vocals_gain":0.25,"instrumental_gain":1,"vocals_muted":false,"instrumental_muted":false}
```

Gains range from 0 to 2, default 1. Muted defaults false. Returns `download_url`
and `filename`. This operation uses the existing desktop MP3 renderer.

```json
{"operation":"export_mp4","track_id":"ID","width":1280,"height":720,"fps":30,"font_size":36,"visible_lines":3,"countdown":true,"celebration":true}
```

Defaults match the example. Even dimensions only: width 320–3840, height 240–2160,
FPS 1–60, font size 12–96, lines 1–8. Uses normalized timed lyrics and the existing
MP4 renderer with instrumental audio. Returns `download_url` and `filename`.

```json
{"operation":"process","path":"Artist/Album","workers":1,"genius_delay":3,"only_align":false}
```

`path` is relative to the shared root (default empty = entire library). Workers:
1–16. Genius delay: 0–300 seconds. `only_align` preserves the CLI's `--only-align`
behavior. The subprocess uses the same interpreter selection as desktop and
retains Linux process-group cancellation.

Statuses: `queued`, `running`, `done`, `error`, `cancelled`. Two workers execute at
most four queued/running jobs. Library mutations are serialized; conflicting
mutations fail with an explanatory error and may be retried. Exports can run
alongside client playback. Logs are bounded to the latest 32 KB. The server keeps
roughly the latest 24 completed jobs; job IDs/downloads expire on server shutdown.
Clients persist their own job ID list. Exports/transposition already running are
allowed to finish on shutdown; processing is terminated.

## Discovery

Send the exact UTF-8 bytes `AI_KARAOKE_DISCOVER_V1` to UDP port 9595 on a LAN
broadcast address. The server replies to the sender with the health JSON.
Verify candidates with `GET /api/health`. Android broadcasts to each interface's
broadcast address and `255.255.255.255`, then performs a bounded parallel HTTP
sweep of local /24 ranges. Manual hostnames/IPs always use port 9595.

## Playback independence

Clients download both stems, decode them, pad shorter stems with silence, then
start both on the same device AudioContext clock. Gain changes, seeks, pauses,
A–B loops and lyrics use that clock. Playback continues from already downloaded
buffers if the server disconnects. History/playlists/preferences are stored in
client local storage; their writes do not reach desktop storage. Library changes
and export resources are shared, but there is no shared playback state.
