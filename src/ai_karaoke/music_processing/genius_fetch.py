from __future__ import annotations

import os
from pathlib import Path
import re
import time

from tqdm import tqdm

from ai_karaoke.services.karaoke_file_service import clean_lyrics_lines

VOCALS_SUFFIX = "_(Vocals)"
GENIUS_LYRICS_SUFFIX = "_(Genius Lyrics)"
MIN_GENIUS_LINES = 5
DEFAULT_GENIUS_DELAY_SECONDS = 30.0
ENV_FILE = Path(".env")

TRAILING_BRACKET_SUFFIX_RE = re.compile(r"\s*(?:\([^()]*\)|\[[^\[\]]*\])\s*$")
FEATURE_SUFFIX_RE = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|п\.у\.|при\s+участии)\s+.+$",
    re.IGNORECASE,
)
FALLBACK_QUERY_TRANSLATION = str.maketrans(
    {
        "ё": "е",
        "Ё": "Е",
        "’": "'",
        "‘": "'",
        "`": "'",
        "´": "'",
        "“": '"',
        "”": '"',
        "«": '"',
        "»": '"',
        "—": "-",
        "–": "-",
        "‑": "-",
        "−": "-",
    }
)


def _is_vocals_mp3(path: Path) -> bool:
    return path.suffix.lower() == ".mp3" and path.stem.endswith(VOCALS_SUFFIX)


def collect_vocals_files(root: Path) -> list[Path]:
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if root.is_file():
        return [root] if _is_vocals_mp3(root) else []
    return sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and _is_vocals_mp3(p)
    )


def _genius_lyrics_output_path(vocals_path: Path) -> Path:
    base = vocals_path.stem
    if base.endswith(VOCALS_SUFFIX):
        base = base[: -len(VOCALS_SUFFIX)]
    return vocals_path.with_name(f"{base}{GENIUS_LYRICS_SUFFIX}.txt")


def _normalize_query_piece(text: str) -> str:
    cleaned = text.strip().replace("_", " ")
    return re.sub(r"\s+", " ", cleaned)


def _dedupe_query_values(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_query_piece(value.strip())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _remove_trailing_bracket_suffixes(title: str) -> str:
    cleaned = title.strip()
    while cleaned:
        updated = TRAILING_BRACKET_SUFFIX_RE.sub("", cleaned).strip()
        if updated == cleaned:
            break
        cleaned = updated
    return cleaned


def _remove_feature_suffix(title: str) -> str:
    cleaned = title.strip()
    return FEATURE_SUFFIX_RE.sub("", cleaned).strip()


def _normalize_song_title_fallback(title: str) -> str:
    cleaned = title.strip().translate(FALLBACK_QUERY_TRANSLATION)
    cleaned = cleaned.replace("_", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def build_song_title_search_attempts(song_title: str) -> list[str]:
    base = song_title.strip()
    if not base:
        return []

    stage_one_candidates = [base]
    without_brackets = _remove_trailing_bracket_suffixes(base)
    if without_brackets:
        stage_one_candidates.append(without_brackets)
    for item in list(stage_one_candidates):
        without_feat = _remove_feature_suffix(item)
        if without_feat:
            stage_one_candidates.append(without_feat)
    stage_one = _dedupe_query_values(stage_one_candidates)

    stage_two_candidates = [
        _normalize_song_title_fallback(item).strip() for item in stage_one
    ]
    stage_two = _dedupe_query_values(stage_two_candidates)

    stage_one_keys = {item.casefold() for item in stage_one}
    fallback_only = [item for item in stage_two if item.casefold() not in stage_one_keys]
    return stage_one + fallback_only


def _cleanup_song_title(stem: str) -> str:
    cleaned = stem.strip()
    cleaned = re.sub(r"^\d+(?:[_-]\d+)?\.\s*", "", cleaned)
    cleaned = re.sub(r"^\d+\s*-\s*", "", cleaned)
    cleaned = re.sub(r"^\d+\s+", "", cleaned)
    return _normalize_query_piece(cleaned)


def _relative_parts_for_query(vocals_path: Path, root: Path) -> list[str]:
    if root.is_dir():
        try:
            return list(vocals_path.relative_to(root).parts)
        except ValueError:
            pass
    if root.is_file():
        try:
            return list(vocals_path.relative_to(root.parent).parts)
        except ValueError:
            pass
    return [vocals_path.parent.name, vocals_path.name]


def infer_artist_and_song(vocals_path: Path, root: Path) -> tuple[str, str]:
    rel_parts = _relative_parts_for_query(vocals_path, root)
    if len(rel_parts) >= 2:
        artist_raw = rel_parts[0]
    else:
        artist_raw = vocals_path.parent.name

    song_filename = rel_parts[-1] if rel_parts else vocals_path.name
    song_stem = Path(song_filename).stem
    if song_stem.endswith(VOCALS_SUFFIX):
        song_stem = song_stem[: -len(VOCALS_SUFFIX)]
    song_title = _cleanup_song_title(song_stem)

    artist = _normalize_query_piece(artist_raw)
    return artist, song_title


def _normalize_lyrics_text(raw: str) -> str:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _format_lines_for_log(lines: list[str]) -> str:
    if not lines:
        return "    <no usable lines>"
    return "\n".join(f"    {idx + 1}. {line}" for idx, line in enumerate(lines))


def _format_genius_debug(debug: dict[str, object] | None) -> str:
    if not debug:
        return "    <no debug details>"

    keys = [
        "phase",
        "artist",
        "song_title",
        "query",
        "selected_song_title",
        "search_attempt_count",
        "attempted_song_titles",
        "fallback_applied",
        "api_song_id",
        "api_song_title",
        "api_primary_artist",
        "api_song_url",
        "lyrics_container_count",
        "lyrics_characters",
        "request_delay_seconds",
        "request_performed",
        "rate_limited",
        "error",
        "script_error",
    ]

    lines: list[str] = []
    for key in keys:
        if key not in debug:
            continue
        value = debug[key]
        if value in ("", None, [], {}):
            continue
        if isinstance(value, list):
            preview = ", ".join(str(item) for item in value[:5])
            if len(value) > 5:
                preview = f"{preview}, ... (+{len(value) - 5} more)"
            lines.append(f"    {key}: {preview}")
            continue
        lines.append(f"    {key}: {value}")

    return "\n".join(lines) if lines else "    <no debug details>"


def _write_empty_lyrics_marker(lyrics_path: Path) -> None:
    lyrics_path.write_text("", encoding="utf-8")


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            item = line.strip()
            if not item or item.startswith("#") or "=" not in item:
                continue
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                loaded[key] = value
    except OSError:
        return {}
    return loaded


def _resolve_genius_access_token() -> str:
    token_keys = (
        "GENIUS_ACCESS_TOKEN",
        "GENIUS_CLIENT_ACCESS_TOKEN",
        "CLIENT_ACCESS_TOKEN",
    )
    for key in token_keys:
        value = os.environ.get(key)
        if value:
            return value.strip()

    env_values = _load_dotenv(ENV_FILE)
    for key in token_keys:
        value = env_values.get(key)
        if value:
            return value.strip()

    return ""


class GeniusLyricsClient:
    def __init__(self) -> None:
        try:
            import lyricsgenius
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "lyricsgenius is unavailable. Install with `uv add lyricsgenius`."
            ) from exc

        token = _resolve_genius_access_token()
        if not token:
            raise RuntimeError(
                "Genius access token is missing. Set GENIUS_ACCESS_TOKEN in .env "
                "or environment."
            )

        self._client = lyricsgenius.Genius(
            token,
            timeout=30,
            retries=2,
            remove_section_headers=True,
            skip_non_songs=True,
            verbose=False,
        )
        self._client.excluded_terms = ["(Remix)", "(Live)"]
        self._client.response_format = "plain"

    def close(self) -> None:
        return

    def _build_debug_context(
        self,
        *,
        artist: str,
        song_title: str,
        lyrics_characters: int | None = None,
        api_song_id: int | None = None,
        api_song_title: str | None = None,
        api_primary_artist: str | None = None,
        api_song_url: str | None = None,
        request_performed: bool | None = None,
        rate_limited: bool | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        debug: dict[str, object] = {
            "phase": "lyricsgenius",
            "artist": artist,
            "song_title": song_title,
        }
        if lyrics_characters is not None:
            debug["lyrics_characters"] = lyrics_characters
        if api_song_id is not None:
            debug["api_song_id"] = api_song_id
        if api_song_title:
            debug["api_song_title"] = api_song_title
        if api_primary_artist:
            debug["api_primary_artist"] = api_primary_artist
        if api_song_url:
            debug["api_song_url"] = api_song_url
        if request_performed is not None:
            debug["request_performed"] = request_performed
        if rate_limited is not None:
            debug["rate_limited"] = rate_limited
        if error:
            debug["error"] = error
        return debug

    def lyrics_for_track(self, artist: str, song_title: str) -> tuple[str, dict[str, object]]:
        request_performed = False
        try:
            request_performed = True
            song = self._client.search_song(title=song_title, artist=artist)
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            return "", self._build_debug_context(
                artist=artist,
                song_title=song_title,
                request_performed=request_performed,
                rate_limited=("429" in message or "rate" in message),
                error=f"lyricsgenius search failed: {type(exc).__name__}: {exc}",
            )

        if song is None:
            return "", self._build_debug_context(
                artist=artist,
                song_title=song_title,
                request_performed=request_performed,
                error="lyricsgenius returned no match",
            )

        raw_lyrics = song.lyrics if isinstance(song.lyrics, str) else ""
        cleaned = re.sub(r"\n?\d+Embed\s*$", "", raw_lyrics).strip()
        normalized_text = _normalize_lyrics_text(cleaned) if cleaned else ""

        debug = self._build_debug_context(
            artist=artist,
            song_title=song_title,
            lyrics_characters=len(normalized_text),
            api_song_id=getattr(song, "id", None),
            api_song_title=getattr(song, "title", None),
            api_primary_artist=getattr(song, "artist", None),
            api_song_url=getattr(song, "url", None),
            request_performed=request_performed,
            error="lyricsgenius returned empty lyrics text" if not normalized_text else None,
        )
        return normalized_text, debug


def fetch_missing_genius_lyrics(root: Path, delay_seconds: float) -> None:
    vocals_files = collect_vocals_files(root)
    if not vocals_files:
        print("No vocals .mp3 files found for Genius lookup.")
        return

    pending: list[tuple[Path, Path]] = []
    for vocals_path in vocals_files:
        lyrics_path = _genius_lyrics_output_path(vocals_path)
        if not lyrics_path.exists():
            pending.append((vocals_path, lyrics_path))

    if not pending:
        print("All vocals files already have Genius lyrics.")
        return

    print(
        f"Genius lyrics missing for {len(pending)} file(s). "
        f"Request delay: {delay_seconds:.1f}s."
    )

    fetcher = GeniusLyricsClient()

    try:
        for idx, (vocals_path, lyrics_path) in enumerate(
            tqdm(pending, desc="Fetching Genius lyrics", unit="file")
        ):
            artist, song_title = infer_artist_and_song(vocals_path, root)
            song_title_attempts = build_song_title_search_attempts(song_title)
            query = " ".join(part for part in (artist, song_title) if part).strip()
            if not artist or not song_title_attempts:
                print(
                    "Warning: could not infer artist/song from path for "
                    f"{vocals_path.resolve()}"
                )
                continue

            request_performed = False
            raw_text = ""
            lyrics_debug: dict[str, object] | None = None

            try:
                for attempt_idx, attempt_song_title in enumerate(song_title_attempts):
                    if attempt_idx > 0 and delay_seconds > 0:
                        print(
                            "Info: waiting "
                            f"{delay_seconds:.1f}s before next Genius request "
                            "(set --genius-delay-seconds 0 for debugging)."
                        )
                        time.sleep(delay_seconds)

                    request_performed = True
                    raw_text, lyrics_debug = fetcher.lyrics_for_track(
                        artist,
                        attempt_song_title,
                    )
                    if lyrics_debug is not None:
                        lyrics_debug["query"] = " ".join(
                            part for part in (artist, attempt_song_title) if part
                        ).strip()
                        lyrics_debug["selected_song_title"] = attempt_song_title
                        lyrics_debug["search_attempt_count"] = len(song_title_attempts)
                        if len(song_title_attempts) > 1:
                            lyrics_debug["attempted_song_titles"] = song_title_attempts
                            lyrics_debug["fallback_applied"] = attempt_idx > 0
                        lyrics_debug["request_delay_seconds"] = delay_seconds
                    if raw_text:
                        break
            except Exception as exc:  # noqa: BLE001
                print(
                    "Warning: failed while fetching Genius lyrics for "
                    f"{vocals_path.resolve()} (query: {query!r}): {exc}\n"
                    f"  Lyrics debug:\n{_format_genius_debug(lyrics_debug)}"
                )

            lines = clean_lyrics_lines(raw_text)
            if len(lines) < MIN_GENIUS_LINES:
                print(
                    "Warning: extracted Genius lyrics are too short; creating empty TXT marker to skip future lookups.\n"
                    f"  Vocals: {vocals_path.resolve()}\n"
                    f"  Target TXT: {lyrics_path.resolve()}\n"
                    f"  Query: {query!r}\n"
                    f"  Artist: {artist!r}\n"
                    f"  Song: {song_title!r}\n"
                    f"  Usable lines: {len(lines)} (minimum {MIN_GENIUS_LINES})\n"
                    f"  Lines:\n{_format_lines_for_log(lines)}\n"
                    f"  Lyrics debug:\n{_format_genius_debug(lyrics_debug)}"
                )
                try:
                    _write_empty_lyrics_marker(lyrics_path)
                except OSError as exc:
                    print(f"Warning: could not write empty marker {lyrics_path.resolve()}: {exc}")
            else:
                try:
                    lyrics_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    print(f"Saved Genius lyrics: {lyrics_path.resolve()}")
                except OSError as exc:
                    print(f"Warning: could not write {lyrics_path.resolve()}: {exc}")

            if (
                request_performed
                and delay_seconds > 0
                and idx < len(pending) - 1
            ):
                print(
                    "Info: waiting "
                    f"{delay_seconds:.1f}s before next Genius request "
                    "(set --genius-delay-seconds 0 for debugging)."
                )
                time.sleep(delay_seconds)
    finally:
        fetcher.close()
