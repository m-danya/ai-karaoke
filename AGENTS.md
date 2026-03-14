# Repository Guide for Agents

This repo is a Python/Tkinter desktop app for playing two-stem MP3s (vocals + instrumental).

## Quick start
- Install deps: `uv sync`
- Run app: `uv run ai-karaoke`
- Library path is stored in `~/.config/ai_karaoke.json` under `library_path`.
- `ffmpeg` must be available in `PATH` for decoding.

## Structure (high level)
- `src/ai_karaoke/` — main application package
- `assets/` — icons and artwork (used by desktop entry)
- `ai-karaoke.desktop` — Linux desktop entry template
- `src/ai_karaoke/audio-separator-script/` — helper scripts for preparing stems
- `build/` — build artifacts
- `recording-description.md` — karaoke recording notes
- `README.md` — install/run notes

## Package layout (`src/ai_karaoke/`)
- `app.py` — entrypoint; reads config and launches UI (`ai_karaoke.app:main`)
- `ui_main.py` — main Tkinter UI (library list, transport, mixer, karaoke recording)
- `player.py` — playback controller + mix state (position, gains, mute state)
- `engine.py` — low-level audio engine (sounddevice callback + mixing)
- `audio.py` — decoding and analysis helpers (ffmpeg decode, vocals envelope)
- `library.py` — library scanning, karaoke path helpers, playlists/history persistence
- `config.py` — config load/save + library path resolution
- `models.py` — dataclasses (e.g., `SongPair`)
- `constants.py` — app constants (tags, defaults, config path)
- `karaoke_screen.py` — fullscreen karaoke UI (currently not wired in)

## Playlists + history (agent notes)
- Playlists are called `playlists` in code/UI.
- Storage location: inside current music library folder as `.ai_karaoke_playlists.json`.
- File schema:
  - `playlists`: object `{playlist_name: [track_id, ...]}`
  - `history`: array `[track_id, ...]`
- `track_id` is relative path to the vocals stem file (relative to library folder).
- UI filter options are `All`, `History`, plus dynamic playlist names.
- `History` is updated automatically when karaoke playback starts.
- Missing tracks in playlist/history views are shown in red and must not be playable.

## Design intent
- UI logic lives in `ui_main.py`.
- Playback/state logic lives in `player.py`/`engine.py`.
- This split is deliberate to allow a second fullscreen screen to reuse the same playback controller without duplicating logic.

## Notes for changes
- Keep `ai_karaoke.app:main` as the entrypoint (pyproject references it).
- Avoid cross-import cycles: UI should depend on `player.py`, not vice versa.
- Use `rg` to search quickly.
- For regular UI text labels, do not add a contrasting background block; text should blend into the parent container.

## Audio separator + lyrics alignment (agent notes)
- Main helper pipeline lives in `src/ai_karaoke/audio-separator-script/main.py`:
  - `separate_mp3s(...)` -> `fetch_missing_genius_lyrics(...)` -> `process_genius_lyrics(...)`.
- CLI supports align-only mode:
  - `--only-align` skips separation/fetch and regenerates karaoke JSON from existing `_(Genius Lyrics).txt`.
- Alignment implementation is in `src/ai_karaoke/audio-separator-script/lyrics_align.py` (`LyricsAligner`).
- Generated karaoke file format (`_(Karaoke Lyrics).json`):
  - JSON array of objects:
    - `line`: original lyric line
    - `start_ts`: line start time (seconds)
    - `end_ts`: line end time (seconds)
    - `words`: per-word timings, each item has `word`, `start_ts`, `end_ts`

## Alignment warning format (what to expect in logs)
- Failed alignment:
  - `Warning: alignment failed for <lyrics_path>: <exception>`
- Empty result:
  - `Warning: alignment produced no segments for <lyrics_path>`
- Count mismatch (non-fatal):
  - `Warning: word count mismatch for <lyrics_path> (expected X, got Y)`

## Alignment troubleshooting checklist
1. Confirm matching vocals exists for the lyrics file (`_find_vocals_for_lyrics` in `main.py`).
2. Inspect cleaned text from `clean_lyrics_lines(...)` (empty/bracket-only lyrics are skipped).
3. Check language auto-detection in `LyricsAligner._resolve_language(...)`.
4. For Cyrillic lyrics, ensure `romanize=True` is used (language must resolve to `rus`).
5. Reproduce on one file first, then rerun batch.

## Known pitfalls and decisions
- Critical regex pitfall (already fixed):
  - Correct Cyrillic detection regex is `re.compile(r"[\u0400-\u04FF]")`.
  - Wrong escaping like `r"[\\u0400-\\u04FF]"` breaks detection and causes frequent `"<star> != <char>"` assertion failures.
- Keep `_get_alignments_safe(...)` wrapper in `lyrics_align.py`:
  - Upstream `ctc_forced_aligner.get_alignments(...)` can fail with MMS model on `<star>` id bounds.
  - Current wrapper aligns dictionary ids with emission vocab size and avoids those crashes.
  - Do not replace it with raw README flow unless validated on the current model/version.
- Keep token sanitization in sync with alignment dictionary:
  - English/mixed lyrics can contain characters not in tokenizer vocab (`-`, `` ` ``, `—`, etc.).
  - If unknown chars are dropped only for `targets` but not for `tokens_starred`, `get_spans` may fail with mismatches like `w != -`.
  - `_sanitize_tokens(...)` must be applied before both forced-align targets and span reconstruction.

## Quick smoke-check commands (audio-separator-script)
- Syntax check:
  - `uv run python -m py_compile src/ai_karaoke/music_processing/lyrics_align.py src/ai_karaoke/music_processing/main.py`
- Single-file alignment check (adjust paths):
  - `uv run python -c "from pathlib import Path; from ai_karaoke.music_processing.lyrics_align import LyricsAligner, clean_lyrics_lines; l=Path('..._(Genius Lyrics).txt'); v=Path('..._(Vocals).mp3'); t=' '.join(clean_lyrics_lines(l.read_text(encoding='utf-8'))); print(len(LyricsAligner().align_word_segments(v,t)))"`
