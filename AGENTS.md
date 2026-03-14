# Repository Guide for Agents

This repo is a Python/Tkinter desktop karaoke app plus an audio-processing CLI.

## Quick start
- Install deps: `uv sync`
- Run desktop app: `uv run ai-karaoke`
- Run processing CLI: `uv run ai-karaoke-process --help`
- Library path is stored in `~/.config/ai_karaoke.json`
- `ffmpeg` must be available in `PATH`

## Current architecture

### Entrypoints
- `src/ai_karaoke/app.py`
  - Desktop bootstrap.
  - Loads `AppSettings`, resolves library path, launches `App`.
- `src/ai_karaoke/ui_main.py`
  - Compatibility facade for the main window.
  - Still contains a lot of orchestration, but shared logic has been extracted.
- `src/ai_karaoke/music_processing/main.py`
  - Compatibility CLI facade.
  - Re-exports the old orchestration surface from smaller modules.

### Core runtime modules
- `src/ai_karaoke/player.py`
  - Playback controller and mix state.
- `src/ai_karaoke/engine.py`
  - Low-level `sounddevice` output engine.
- `src/ai_karaoke/audio.py`
  - ffmpeg-backed decode/transcode/mix helpers.
- `src/ai_karaoke/models.py`
  - Shared typed models for song pairs, track list items, export settings, karaoke entries.
- `src/ai_karaoke/settings.py`
  - Typed application settings and config persistence.

### Library/storage modules
- `src/ai_karaoke/library_scan.py`
  - Finds vocals/instrumental pairs in the library.
- `src/ai_karaoke/library_paths.py`
  - Shared path helpers for karaoke/genius/track-id/base-name logic.
- `src/ai_karaoke/playlist_store.py`
  - Reads/writes playlists and history.
- `src/ai_karaoke/library.py`
  - Compatibility facade exporting the old library API.

### UI/shared helper modules
- `src/ai_karaoke/karaoke_screen.py`
  - Fullscreen karaoke UI facade.
- `src/ai_karaoke/ui/widgets/formatting.py`
  - Shared formatting helpers such as `format_time`.
- `src/ai_karaoke/ui/widgets/scale_helpers.py`
  - Shared slider/scroll math used by both windows.
- `src/ai_karaoke/ui/karaoke/tooltips.py`
  - Shared hover tooltip widget for karaoke UI.

### Services
- `src/ai_karaoke/services/karaoke_file_service.py`
  - Karaoke JSON load/normalize logic and shared `clean_lyrics_lines`.
- `src/ai_karaoke/services/export_service.py`
  - MP3 render orchestration and export destination validation.
- `src/ai_karaoke/services/transpose_service.py`
  - Transposed-track creation and rollback.
- `src/ai_karaoke/services/system_integration.py`
  - File manager and browser integration.

### Controllers
- `src/ai_karaoke/controllers/process_runner.py`
  - Subprocess/thread/queue lifecycle for the music-processing runner.

### Music processing pipeline
- `src/ai_karaoke/music_processing/cli.py`
  - Argument parsing.
- `src/ai_karaoke/music_processing/pipeline.py`
  - Top-level orchestration.
- `src/ai_karaoke/music_processing/separation.py`
  - Stem separation worker flow.
- `src/ai_karaoke/music_processing/alignment_pipeline.py`
  - Karaoke alignment orchestration.
- `src/ai_karaoke/music_processing/cache.py`
  - Model cache and warmup.
- `src/ai_karaoke/music_processing/io_paths.py`
  - Processing path/suffix helpers.
- `src/ai_karaoke/music_processing/genius_fetch.py`
  - Genius lyrics lookup.
- `src/ai_karaoke/music_processing/lyrics_align.py`
  - Forced-alignment engine.

## Layering rules
- `ui/*` and `karaoke_screen.py` are for widgets, layout, rendering, and UI event plumbing.
- `controllers/*` are for orchestration between UI and long-running side effects.
- `services/*` are for reusable side-effectful or domain-specific operations.
- `models.py` and typed data objects must stay UI-agnostic.
- `music_processing/*` must stay decoupled from desktop UI modules.
- `library_paths.py` is the shared source of truth for karaoke/genius/track-id path conventions.
- `settings.py` is the shared source of truth for persisted app settings.

## Do not put new logic in these places
- Do not add new domain logic directly into `ui_main.py` if it can live in `services/*` or `controllers/*`.
- Do not duplicate helper logic between `ui_main.py` and `karaoke_screen.py`.
- Do not reintroduce config parsing into UI classes; use `AppSettings`.
- Do not put playlist/history storage logic back into widgets.
- Do not put music-processing orchestration back into `music_processing/main.py`.
- Do not use `import *` outside the existing compatibility wrapper files.

## Playlists and history
- Storage file: `.ai_karaoke_playlists.json` in the current library folder.
- Schema:
  - `playlists`: object `{playlist_name: [track_id, ...]}`
  - `history`: array `[track_id, ...]`
- Stored ids are relative when possible; runtime ids are normalized absolute paths.
- UI filters are `All`, `History`, plus dynamic playlist names.
- Missing tracks in playlist/history views must stay visible, red, and non-playable.
- `History` is updated when karaoke playback starts.

## Karaoke JSON format
- File suffix: `_(Karaoke Lyrics).json`
- JSON value: array of objects
  - `line`
  - `start_ts`
  - `end_ts`
  - `words`: array of `{word, start_ts, end_ts}`
- Keep format backward compatible.
- Shared line cleaning lives in `services/karaoke_file_service.py`.

## Processing CLI notes
- Preserve `ai_karaoke.music_processing.main:main` as the CLI entrypoint.
- `--only-align` must keep working.
- Alignment warnings should keep their current wording shape:
  - `Warning: alignment failed for <lyrics_path>: <exception>`
  - `Warning: alignment produced no segments for <lyrics_path>`
  - `Warning: word count mismatch for <lyrics_path> (expected X, got Y)`

## Known implementation decisions
- Correct Cyrillic detection regex is `re.compile(r"[\u0400-\u04FF]")`.
- Keep `_get_alignments_safe(...)` in `lyrics_align.py`.
- Keep token sanitization aligned with the tokenizer dictionary.
- Keep `ui_main.py` and `music_processing/main.py` as compatibility facades unless explicitly asked to break imports.

## Change guidance
- Prefer extending existing services/controllers before adding more logic to the `App` class.
- When adding a new reusable helper, put it in a dedicated module instead of duplicating it locally.
- When changing path conventions, update `library_paths.py` and every caller through that module.
- When changing karaoke file semantics, update both desktop loading and processing generation paths.
- When changing subprocess behavior, keep Linux process-group kill semantics intact.

## Quick smoke checks
- Syntax check:
  - `python3 -m py_compile $(find src/ai_karaoke -name '*.py' -print)`
- Import smoke check in venv:
  - `./.venv/bin/python -c "import ai_karaoke.app, ai_karaoke.ui_main, ai_karaoke.karaoke_screen, ai_karaoke.music_processing.main"`
