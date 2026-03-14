# Refactoring Plan Part 2

## Goal

В первом этапе были вынесены shared helpers, settings, path/storage helpers, базовые services и часть processing pipeline. Во втором этапе нужно убрать оставшиеся god-class/god-module точки:

- `src/ai_karaoke/ui_main.py` — все еще основной монолит (`3324` строк)
- `src/ai_karaoke/karaoke_screen.py` — все еще крупный fullscreen UI фасад (`1259` строк)
- `src/ai_karaoke/music_processing/genius_fetch.py` — толстый модуль с несколькими зонами ответственности (`490` строк)
- `src/ai_karaoke/music_processing/lyrics_align.py` — смешивает engine logic и post-processing (`226` строк)

Цель part 2: довести архитектуру до состояния, где `ui_main.py`, `karaoke_screen.py` и `music_processing/main.py` остаются только thin facade / composition root модулями.

## Current state after part 1

Уже вынесены:

- `settings.py`
- `library_paths.py`
- `library_scan.py`
- `playlist_store.py`
- `services/export_service.py`
- `services/transpose_service.py`
- `services/system_integration.py`
- `services/karaoke_file_service.py`
- `controllers/process_runner.py`
- `music_processing/{cli,pipeline,separation,alignment_pipeline,cache,io_paths}.py`
- `ui/widgets/{formatting,scale_helpers}.py`
- `ui/karaoke/tooltips.py`

Это дает нормальные швы. Дальше надо распиливать по behavior-level подсистемам, а не просто переносить функции по файлам.

## Phase 1: split `ui_main.py` into state + controllers + window widgets

### 1.1 Create explicit app state objects

Добавить typed state модули:

- `src/ai_karaoke/controllers/app_state.py`
- `src/ai_karaoke/controllers/karaoke_state.py`
- `src/ai_karaoke/controllers/recording_state.py`
- `src/ai_karaoke/controllers/library_state.py`

В них вынести mutable state, который сейчас лежит прямо на `App`:

- current track selection
- filtered items
- karaoke entries / idx hints / loop markers
- processing flags
- export/transposition progress flags
- recording progress and result buffers

Критерий:
- `App.__init__` больше не содержит длинный список несвязанных `_foo` полей.
- State сгруппирован по подсистемам.

### 1.2 Extract main window layout

Создать:

- `src/ai_karaoke/ui/main_window.py`
- `src/ai_karaoke/ui/theme.py`

Вынести из `ui_main.py`:

- `_apply_theme`
- `_build_ui`
- создание и хранение widget refs
- pack/grid layout обычного окна

`MainWindow` должен:

- создавать все виджеты main window
- хранить widget refs
- не знать о library scanning, karaoke parsing, process lifecycle

`App` должен:

- создавать `MainWindow`
- подписывать callbacks
- заполнять UI данными

Критерий:
- `ui_main.py` перестает напрямую строить layout.
- Theme находится вне `App`.

### 1.3 Extract library/browse/filter/playlists controller

Создать:

- `src/ai_karaoke/controllers/library_controller.py`

Перенести туда:

- scan/rescan orchestration
- filter/search application
- list rebuilding decisions
- playlist/history mutations
- missing-track handling rules
- context-menu actions

`library_controller.py` должен работать через:

- `library_scan.py`
- `library_paths.py`
- `playlist_store.py`
- `models.py`

В `App` оставить только binding UI events к controller methods.

Критерий:
- `ui_main.py` больше не содержит playlist/history CRUD и filtering logic.

### 1.4 Extract playback/loading controller

Создать:

- `src/ai_karaoke/controllers/playback_controller.py`

Перенести туда:

- `_load_pair`
- async decode/load worker
- `_finish_load`
- `_toggle_play_pause`
- seek handling
- autoplay next-track logic
- vocal scope source-state updates

Этот controller использует существующий `PlaybackController` из `player.py`, но сам отвечает за UI-facing orchestration.

Критерий:
- `App` не управляет загрузкой треков напрямую.
- Audio decode worker logic исчезает из `ui_main.py`.

### 1.5 Extract process UI controller

`controllers/process_runner.py` уже есть, теперь нужен следующий слой:

- `src/ai_karaoke/controllers/process_controller.py`
- `src/ai_karaoke/ui/dialogs/process_dialogs.py`

Вынести туда:

- process settings dialog
- process log window
- poll scheduling
- close/kill confirmation flow
- button-state synchronization

Критерий:
- `ui_main.py` не содержит код создания process dialogs.
- `App` не работает с raw process log widgets.

### 1.6 Extract export/transposition controller

Создать:

- `src/ai_karaoke/controllers/export_controller.py`
- `src/ai_karaoke/ui/dialogs/export_dialogs.py`

Перенести туда:

- save-as-mp3 flow
- transpose flow
- progress dialogs
- destination/mix-mode dialogs
- rescan-after-success behavior

`export_controller.py` использует:

- `services/export_service.py`
- `services/transpose_service.py`

Критерий:
- `ui_main.py` не содержит rendering/transposition progress window code.

## Phase 2: split karaoke and recording orchestration out of `ui_main.py`

### 2.1 Create karaoke playback controller

Добавить:

- `src/ai_karaoke/controllers/karaoke_controller.py`
- `src/ai_karaoke/ui/karaoke/view_model.py`

Перенести туда:

- countdown logic
- loop in/out/clear logic
- karaoke file loading orchestration
- current lyric line / word progress calculation
- finish celebration logic
- history update trigger on karaoke start
- fullscreen open/close behavior coordination

Критерий:
- `ui_main.py` не содержит `_karaoke_display_state`, `_apply_karaoke_loop`, `_run_karaoke_countdown`, `_load_karaoke_playback`.
- `KaraokeScreen` получает уже рассчитанный view model, а не считает бизнес-логику сам.

### 2.2 Create recording controller

Добавить:

- `src/ai_karaoke/controllers/recording_controller.py`

Перенести туда:

- manual lyrics prompt orchestration
- recording progress
- next-line timestamps
- instrumental break markers
- saving recorded karaoke JSON
- temporary recording-state cleanup

Критерий:
- `ui_main.py` больше не хранит запись как отдельный самописный state-machine.

## Phase 3: split `karaoke_screen.py` into components

### 3.1 Window shell

Создать:

- `src/ai_karaoke/ui/karaoke/window_shell.py`

Туда вынести:

- fullscreen `Toplevel`
- open/close/show/focus behavior
- top-level resize trigger

### 3.2 Lyrics renderer

Создать:

- `src/ai_karaoke/ui/karaoke/lyrics_canvas.py`

Туда вынести:

- slot creation
- token splitting
- line wrapping
- per-word canvas item creation
- active word coloring / progress fill
- layout cache invalidation

### 3.3 Controls panels

Создать:

- `src/ai_karaoke/ui/karaoke/control_panel.py`
- `src/ai_karaoke/ui/karaoke/record_panel.py`

Туда вынести:

- playback controls
- mix controls
- tools / loop buttons
- scope area
- recording buttons and status panel

### 3.4 Facade cleanup

После переноса:

- `karaoke_screen.py` должен остаться thin facade
- он должен собирать shell + panels + renderer
- публичный API `KaraokeScreen` оставить совместимым

Критерий:
- `karaoke_screen.py` <= 300-400 строк
- отдельные UI responsibilities разнесены по компонентам

## Phase 4: finish the processing-layer split

### 4.1 Split `genius_fetch.py`

Добавить:

- `src/ai_karaoke/music_processing/env.py`
- `src/ai_karaoke/music_processing/genius_query.py`
- `src/ai_karaoke/music_processing/genius_client.py`

Распределение:

- `env.py`
  - `.env` loading
  - token resolving
- `genius_query.py`
  - query normalization
  - title fallback strategies
  - infer artist/song from path
- `genius_client.py`
  - `GeniusLyricsClient`
  - response/debug formatting tied to HTTP client behavior

В `genius_fetch.py` оставить только batch orchestration.

Критерий:
- `genius_fetch.py` <= 180-220 строк

### 4.2 Split `lyrics_align.py`

Добавить:

- `src/ai_karaoke/music_processing/aligner_engine.py`
- `src/ai_karaoke/music_processing/karaoke_builder.py`

Распределение:

- `aligner_engine.py`
  - `AlignmentConfig`
  - `LyricsAligner`
  - tokenizer / emission / alignment safety wrappers
- `karaoke_builder.py`
  - `build_karaoke_entries`
  - any alignment-result to karaoke-json transformations

В `lyrics_align.py` оставить compatibility facade или thin re-export.

Критерий:
- forced-alignment engine и karaoke post-processing не смешаны.

## Phase 5: remove remaining compatibility bulk from facades

После всех переносов:

- `ui_main.py`
  - only `App` composition root
  - wiring between window, controllers, settings, player
- `karaoke_screen.py`
  - thin facade around karaoke UI components
- `music_processing/main.py`
  - thin CLI facade only
- `library.py`
  - keep facade, but no local duplicated logic

Критерий:

- `ui_main.py` <= 800-1000 строк
- `karaoke_screen.py` <= 300-400 строк
- `music_processing/main.py` <= 80 строк
- no duplicated helpers between main window and fullscreen karaoke

## Acceptance criteria

После part 2 должны сохраниться без регрессий:

- `uv run ai-karaoke`
- `uv run ai-karaoke-process --help`
- library rescan / search / filter / playlists / history
- play / pause / seek / mute / full volume
- karaoke fullscreen
- karaoke countdown, loop points, finish celebration
- manual karaoke recording flow
- process dialog + log + kill flow
- transpose flow
- save-as-mp3 flow
- storage compatibility:
  - `~/.config/ai_karaoke.json`
  - `.ai_karaoke_playlists.json`
  - `_(Karaoke Lyrics).json`

## Suggested implementation order

1. `theme.py` + `main_window.py`
2. `library_controller.py`
3. `playback_controller.py`
4. `process_controller.py` + process dialogs
5. `export_controller.py` + export dialogs
6. `karaoke_controller.py`
7. `recording_controller.py`
8. karaoke UI component split
9. `genius_fetch.py` split
10. `lyrics_align.py` split
11. final facade cleanup
12. update `AGENTS.md` again after the tree actually changes

## Non-goals for part 2

- Не менять on-disk formats
- Не менять entrypoint names
- Не переписывать playback engine
- Не вводить новый UI toolkit
- Не смешивать feature work с архитектурным переносом

## Practical rule during implementation

Каждый шаг должен быть mergeable отдельно:

- сначала move/extract
- потом переключение callers
- потом удаление старого кода

Не делать big-bang rewrite `ui_main.py` за один патч.
