from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from ..audio import probe_audio_duration
from ..models import KaraokeEntry, KaraokeFrameState, KaraokeRenderSettings, SongPair, VideoExportSettings
from ..ui.karaoke.video_renderer import KaraokeVideoRenderer
from .export_service import validate_export_destination
from .karaoke_timeline_service import (
    countdown_frame_state,
    finish_frame_state,
    karaoke_end_timestamps,
    karaoke_frame_state,
)

ProgressCallback = Callable[[str], None]

_FINISH_SCREEN_SECONDS = 2.0


def validate_video_export_destination(pair: SongPair, output_path: Path) -> None:
    validate_export_destination(pair, output_path)


def render_karaoke_to_mp4(
    pair: SongPair,
    output_path: Path,
    entries: Sequence[KaraokeEntry],
    *,
    title: str,
    colors: dict[str, str],
    render_settings: KaraokeRenderSettings,
    video_settings: VideoExportSettings,
    finish_message: str | None = None,
    progress_callback: ProgressCallback | None = None,
    sr: int,
) -> None:
    video_temp: Path | None = None
    try:
        if progress_callback is not None:
            progress_callback("Analyzing instrumental audio...")
        audio_duration = probe_audio_duration(pair.instrumental, sr=sr)
        countdown_seconds = 3.0 if render_settings.countdown_enabled else 0.0
        finish_seconds = _FINISH_SCREEN_SECONDS if render_settings.finish_celebration_enabled and finish_message else 0.0
        total_duration = countdown_seconds + audio_duration + finish_seconds
        total_frames = max(1, int(math.ceil(total_duration * video_settings.fps)))
        timeline = karaoke_end_timestamps(entries)
        renderer = KaraokeVideoRenderer(
            colors,
            render_settings,
            width=video_settings.width,
            height=video_settings.height,
            title=title,
        )

        if progress_callback is not None:
            progress_callback(f"Rendering video frames (0/{total_frames})...")
        with tempfile.NamedTemporaryFile(
            prefix=f"{output_path.stem}.",
            suffix=".mp4",
            dir=str(output_path.parent),
            delete=False,
        ) as handle:
            video_temp = Path(handle.name)

        _encode_video_frames(
            renderer=renderer,
            audio_path=pair.instrumental,
            output_path=video_temp,
            entries=entries,
            timeline=timeline,
            render_settings=render_settings,
            video_settings=video_settings,
            countdown_seconds=countdown_seconds,
            audio_duration=audio_duration,
            finish_seconds=finish_seconds,
            finish_message=finish_message,
            total_frames=total_frames,
            progress_callback=progress_callback,
        )
        video_temp.replace(output_path)
    except Exception:
        if video_temp is not None:
            try:
                video_temp.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        raise


def _encode_video_frames(
    *,
    renderer: KaraokeVideoRenderer,
    audio_path: Path,
    output_path: Path,
    entries: Sequence[KaraokeEntry],
    timeline: Sequence[float],
    render_settings: KaraokeRenderSettings,
    video_settings: VideoExportSettings,
    countdown_seconds: float,
    audio_duration: float,
    finish_seconds: float,
    finish_message: str | None,
    total_frames: int,
    progress_callback: ProgressCallback | None,
) -> None:
    cmd = _video_encode_command(
        audio_path=audio_path,
        output_path=output_path,
        width=video_settings.width,
        height=video_settings.height,
        fps=video_settings.fps,
        audio_delay_seconds=countdown_seconds,
        audio_pad_seconds=finish_seconds,
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    stderr = ""
    try:
        if proc.stdin is None:
            raise RuntimeError("ffmpeg stdin is unavailable")
        for frame_idx in range(total_frames):
            t = frame_idx / video_settings.fps
            state = _frame_state_for_time(
                entries=entries,
                timeline=timeline,
                render_settings=render_settings,
                t=t,
                countdown_seconds=countdown_seconds,
                audio_duration=audio_duration,
                finish_message=finish_message,
            )
            image = renderer.render_frame(state)
            proc.stdin.write(image.tobytes())
            if progress_callback is not None and (
                frame_idx == 0
                or frame_idx == total_frames - 1
                or (frame_idx + 1) % max(1, video_settings.fps) == 0
            ):
                progress_callback(f"Rendering video frames ({frame_idx + 1}/{total_frames})...")
        proc.stdin.close()
        proc.stdin = None
        stderr = (proc.stderr.read() or b"").decode("utf-8", errors="replace") if proc.stderr else ""
        retcode = proc.wait()
        if retcode != 0:
            raise subprocess.CalledProcessError(retcode, cmd, stderr=stderr)
    except Exception:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stderr is not None:
            try:
                proc.stderr.close()
            except OSError:
                pass


def _frame_state_for_time(
    *,
    entries: Sequence[KaraokeEntry],
    timeline: Sequence[float],
    render_settings: KaraokeRenderSettings,
    t: float,
    countdown_seconds: float,
    audio_duration: float,
    finish_message: str | None,
) -> KaraokeFrameState:
    if countdown_seconds > 0.0 and t < countdown_seconds:
        countdown_value = max(1, int(math.ceil(countdown_seconds - t)))
        return countdown_frame_state(render_settings.visible_lines, countdown_value)
    lyric_t = max(0.0, t - countdown_seconds)
    if lyric_t < audio_duration:
        return karaoke_frame_state(
            entries,
            visible_lines=render_settings.visible_lines,
            t=lyric_t,
            end_timestamps=timeline,
        )
    if finish_message:
        return finish_frame_state(render_settings.visible_lines, finish_message)
    return karaoke_frame_state(
        entries,
        visible_lines=render_settings.visible_lines,
        t=audio_duration,
        end_timestamps=timeline,
    )


def _video_encode_command(
    *,
    audio_path: Path,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    audio_delay_seconds: float,
    audio_pad_seconds: float,
) -> list[str]:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-i",
        str(audio_path),
        "-filter_complex",
        _audio_filter_chain(audio_delay_seconds=audio_delay_seconds, audio_pad_seconds=audio_pad_seconds),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
    ]
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    )
    return cmd


def _audio_filter_chain(*, audio_delay_seconds: float, audio_pad_seconds: float) -> str:
    filters = [f"adelay={int(round(audio_delay_seconds * 1000.0))}:all=1"]
    if audio_pad_seconds > 0.0:
        filters.append(f"apad=pad_dur={audio_pad_seconds:.3f}")
    return f"[1:a]{','.join(filters)}[aout]"
