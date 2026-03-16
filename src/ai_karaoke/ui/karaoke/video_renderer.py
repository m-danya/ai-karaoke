from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from ...models import KaraokeFrameState, KaraokeRenderSettings


class KaraokeVideoRenderer:
    _FOOTER_TEXT = "m-danya/ai-karaoke"
    _FOOTER_LOGO_PATH = Path(__file__).resolve().parents[4] / "assets" / "logo-transparent.png"

    def __init__(
        self,
        colors: dict[str, str],
        render_settings: KaraokeRenderSettings,
        *,
        width: int,
        height: int,
        title: str,
    ) -> None:
        self.colors = colors
        self.render_settings = render_settings
        self.width = int(width)
        self.height = int(height)
        self.title = str(title).strip()
        self._font_scale = max(1.0, float(render_settings.tk_scaling))
        lyrics_font_size = _font_pixel_size(render_settings.font_size, self._font_scale, minimum=20)
        title_font_size = _font_pixel_size(
            render_settings.font_size * 0.55,
            self._font_scale,
            minimum=22,
        )
        footer_font_size = _font_pixel_size(
            render_settings.font_size * 0.38,
            self._font_scale,
            minimum=14,
        )
        self._lyrics_font = _load_font(
            _font_patterns(
                [
                    render_settings.lyrics_font_family,
                    "Playfair Display",
                    "Noto Sans",
                    "DejaVu Sans",
                    "Noto Serif",
                    "DejaVu Serif",
                ],
                bold=True,
            ),
            size=lyrics_font_size,
        )
        self._lyrics_stroke_width = _font_stroke_width(lyrics_font_size)
        self._title_font = _load_font(
            _font_patterns(
                [
                    render_settings.title_font_family,
                    "Fira Sans",
                    "DejaVu Sans",
                    "Noto Sans",
                ],
                bold=True,
            ),
            size=title_font_size,
        )
        self._footer_font = _load_font(
            _font_patterns(
                [
                    render_settings.footer_font_family,
                    "Fira Sans",
                    "DejaVu Sans",
                    "Noto Sans",
                ],
                bold=False,
            ),
            size=footer_font_size,
        )
        self._measure_image = Image.new("RGB", (1, 1))
        self._measure_draw = ImageDraw.Draw(self._measure_image)
        self._outer_pad_x = max(48, int(self.width * 0.055))
        self._top_pad = max(44, int(self.height * 0.05))
        self._bottom_pad = max(20, int(self.height * 0.025))
        self._title_gap = max(18, int(self.height * 0.025))
        self._footer_gap = max(14, int(self.height * 0.018))
        self._footer_text_bbox = self._text_bbox(self._FOOTER_TEXT, font=self._footer_font)
        self._footer_height = max(1, int(self._footer_text_bbox[3] - self._footer_text_bbox[1]))
        self._footer_logo = _load_footer_logo(max(self._footer_height, int(round(self._footer_height * 1.35))))
        self._footer_logo_gap = max(8, int(round(self._footer_height * 0.45)))
        self._line_height = max(1, _text_height(self._lyrics_font, "Ag", stroke_width=self._lyrics_stroke_width))
        self._line_gap = max(6, int(self._line_height * 0.26))
        self._display_gap = max(2, int(self._line_height * 0.09))
        self._space_width = max(1, self._measure_text(" "))
        self._line_pad_x = max(24, int(self.width * 0.02))

    def render_frame(self, state: KaraokeFrameState) -> Image.Image:
        image = Image.new("RGB", (self.width, self.height), self.colors["bg"])
        draw = ImageDraw.Draw(image)
        title_bottom = self._draw_title(draw)
        footer_top = self._draw_footer(image, draw)
        lyrics_top = title_bottom + self._title_gap
        lyrics_bottom = footer_top - self._footer_gap
        lyrics_area_height = max(120, lyrics_bottom - lyrics_top)
        slot_height = self._slot_height(lyrics_area_height)
        total_slots_height = (
            self.render_settings.visible_lines * slot_height
            + max(0, self.render_settings.visible_lines - 1) * self._display_gap
        )
        start_y = lyrics_top + max(0, (lyrics_area_height - total_slots_height) // 2)

        for slot_idx in range(self.render_settings.visible_lines):
            slot_top = start_y + slot_idx * (slot_height + self._display_gap)
            slot_text = state.slot_lines[slot_idx] if slot_idx < len(state.slot_lines) else ""
            is_active = slot_idx == state.active_slot
            if is_active and state.words:
                tokens = [token for token in state.words if token]
                self._draw_tokens(
                    draw,
                    slot_top=slot_top,
                    slot_height=slot_height,
                    tokens=tokens,
                    base_color=self.colors["text"],
                    sung_words=state.sung_words,
                    active_word_idx=state.active_word_idx,
                    active_word_progress=state.active_word_progress,
                )
                continue

            tokens = [token for token in str(slot_text).split() if token]
            self._draw_tokens(
                draw,
                slot_top=slot_top,
                slot_height=slot_height,
                tokens=tokens,
                base_color=self.colors["text"] if is_active else self.colors["muted"],
                sung_words=0,
                active_word_idx=None,
                active_word_progress=0.0,
            )
        return image

    def _draw_title(self, draw: ImageDraw.ImageDraw) -> int:
        if not self.title:
            return self._top_pad
        title_width = max(320, self.width - self._outer_pad_x * 2)
        lines = self._wrap_tokens(self.title.split(), self._title_font, title_width)
        line_height = max(1, _text_height(self._title_font, "Ag"))
        gap = max(6, int(line_height * 0.25))
        total_height = len(lines) * line_height + max(0, len(lines) - 1) * gap
        y = self._top_pad
        for idx, line in enumerate(lines):
            line_w = self._measure_text(line, font=self._title_font)
            x = int((self.width - line_w) / 2)
            draw.text((x, y), line, font=self._title_font, fill=self.colors["text"])
            y += line_height + (gap if idx < len(lines) - 1 else 0)
        return self._top_pad + total_height

    def _draw_footer(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> int:
        footer_width = max(0, int(self._footer_text_bbox[2] - self._footer_text_bbox[0]))
        logo = self._footer_logo
        block_height = self._footer_height
        block_width = footer_width
        if logo is not None:
            block_height = max(block_height, logo.height)
            block_width += logo.width + self._footer_logo_gap
        footer_y = self.height - self._bottom_pad - block_height
        footer_x = int((self.width - block_width) / 2)
        if logo is not None:
            logo_y = footer_y + max(0, int((block_height - logo.height) / 2))
            image.paste(logo, (footer_x, logo_y), logo)
            footer_x += logo.width + self._footer_logo_gap
        text_x = footer_x - int(self._footer_text_bbox[0])
        text_y = footer_y + max(0, int((block_height - self._footer_height) / 2)) - int(
            self._footer_text_bbox[1]
        )
        draw.text(
            (text_x, text_y),
            self._FOOTER_TEXT,
            font=self._footer_font,
            fill=self.colors.get("muted", self.colors["text"]),
        )
        return footer_y

    def _slot_height(self, lyrics_area_height: int) -> int:
        line_count = max(1, self.render_settings.visible_lines)
        max_slot_height = int((lyrics_area_height * 0.96) / line_count)
        return max(
            self._line_height * 2 + self._line_gap,
            min(max_slot_height, max(110, int(self.height * 0.175))),
        )

    def _draw_tokens(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        slot_top: int,
        slot_height: int,
        tokens: Sequence[str],
        base_color: str,
        sung_words: int,
        active_word_idx: int | None,
        active_word_progress: float,
    ) -> None:
        if not tokens:
            return

        layout_width = max(240, self.width - self._outer_pad_x * 2 - self._line_pad_x * 2)
        token_widths = [self._measure_text(token) for token in tokens]
        lines, line_widths = _wrap_token_widths(token_widths, self._space_width, layout_width)
        total_height = len(lines) * self._line_height + max(0, len(lines) - 1) * self._line_gap
        y = slot_top + max(0, int((slot_height - total_height) / 2))
        frame_width = max(320, self.width - self._outer_pad_x * 2)

        for line_idx, line in enumerate(lines):
            line_width = line_widths[line_idx]
            x = self._outer_pad_x + max(self._line_pad_x, int((frame_width - line_width) / 2))
            for pos, token_idx in enumerate(line):
                if pos > 0:
                    x += self._space_width
                token = tokens[token_idx]
                token_width = token_widths[token_idx]
                self._draw_token(
                    draw,
                    token=token,
                    x=x,
                    y=y,
                    base_color=base_color,
                    is_sung=token_idx < max(0, int(sung_words)),
                    is_active=active_word_idx == token_idx,
                    active_word_progress=active_word_progress,
                )
                x += token_width
            y += self._line_height + self._line_gap

    def _draw_token(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        token: str,
        x: int,
        y: int,
        base_color: str,
        is_sung: bool,
        is_active: bool,
        active_word_progress: float,
    ) -> None:
        if is_sung:
            self._draw_lyrics_text(draw, x=x, y=y, text=token, fill=self.colors["accent"])
            return

        self._draw_lyrics_text(draw, x=x, y=y, text=token, fill=base_color)
        if not is_active:
            return

        progress = min(max(float(active_word_progress), 0.0), 1.0)
        chars = min(len(token), max(0, int(len(token) * progress)))
        if chars <= 0:
            return
        if chars >= len(token):
            self._draw_lyrics_text(draw, x=x, y=y, text=token, fill=self.colors["accent"])
            return
        self._draw_lyrics_text(draw, x=x, y=y, text=token[:chars], fill=self.colors["karaoke"])

    def _draw_lyrics_text(
        self,
        draw: ImageDraw.ImageDraw,
        *,
        x: int,
        y: int,
        text: str,
        fill: str,
    ) -> None:
        draw.text(
            (x, y),
            text,
            font=self._lyrics_font,
            fill=fill,
            stroke_width=self._lyrics_stroke_width,
            stroke_fill=fill,
        )

    def _measure_text(self, text: str, *, font: ImageFont.ImageFont | None = None) -> int:
        bbox = self._text_bbox(text, font=font)
        return max(0, int(bbox[2] - bbox[0]))

    def _text_bbox(
        self,
        text: str,
        *,
        font: ImageFont.ImageFont | None = None,
    ) -> tuple[int, int, int, int]:
        active_font = font or self._lyrics_font
        if not text:
            return (0, 0, 0, 0)
        bbox = self._measure_draw.textbbox(
            (0, 0),
            text,
            font=active_font,
            stroke_width=self._stroke_width_for_font(active_font),
        )
        return tuple(int(value) for value in bbox)

    def _stroke_width_for_font(self, font: ImageFont.ImageFont) -> int:
        return self._lyrics_stroke_width if font is self._lyrics_font else 0

    def _wrap_tokens(
        self,
        tokens: Sequence[str],
        font: ImageFont.ImageFont,
        max_width: int,
    ) -> list[str]:
        if not tokens:
            return [""]
        lines: list[list[str]] = []
        current: list[str] = []
        current_width = 0
        space_width = max(1, self._measure_text(" ", font=font))
        for token in tokens:
            token_width = self._measure_text(token, font=font)
            if not current:
                current = [token]
                current_width = token_width
                continue
            next_width = current_width + space_width + token_width
            if next_width > max_width:
                lines.append(current)
                current = [token]
                current_width = token_width
                continue
            current.append(token)
            current_width = next_width
        if current:
            lines.append(current)
        return [" ".join(parts) for parts in lines]


def _text_height(font: ImageFont.ImageFont, text: str, *, stroke_width: int = 0) -> int:
    bbox = font.getbbox(text, stroke_width=stroke_width)
    return max(1, int(bbox[3] - bbox[1]))


def _wrap_token_widths(
    token_widths: Sequence[int],
    space_width: int,
    max_width: int,
) -> tuple[list[list[int]], list[int]]:
    lines: list[list[int]] = []
    line_widths: list[int] = []
    current_line: list[int] = []
    current_width = 0
    for idx, token_width in enumerate(token_widths):
        if not current_line:
            current_line = [idx]
            current_width = token_width
            continue
        next_width = current_width + space_width + token_width
        if next_width > max_width:
            lines.append(current_line)
            line_widths.append(current_width)
            current_line = [idx]
            current_width = token_width
            continue
        current_line.append(idx)
        current_width = next_width
    if current_line:
        lines.append(current_line)
        line_widths.append(current_width)
    return lines, line_widths


def _font_patterns(families: Sequence[str], *, bold: bool) -> list[str]:
    patterns: list[str] = []
    seen: set[str] = set()
    for family in families:
        normalized = str(family).strip()
        if not normalized:
            continue
        key = _normalize_family_name(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        if bold:
            patterns.append(f"{normalized}:style=Bold")
        patterns.append(normalized)
    return patterns


def _normalize_family_name(name: str) -> str:
    return "".join(char for char in name.casefold() if char.isalnum())


def _family_name_matches(requested: str, matched: str) -> bool:
    requested_key = _normalize_family_name(requested)
    matched_key = _normalize_family_name(matched)
    return bool(requested_key and matched_key) and (
        requested_key == matched_key
        or requested_key in matched_key
        or matched_key in requested_key
    )


def _requested_family(pattern: str) -> str:
    return pattern.split(":", 1)[0].strip()


def _font_pixel_size(point_size: float, scale: float, *, minimum: int) -> int:
    return max(int(minimum), int(round(float(point_size) * float(scale))))


def _font_stroke_width(size: int) -> int:
    return max(1, int(round(float(size) / 48.0)))


@lru_cache(maxsize=32)
def _font_match(pattern: str) -> tuple[tuple[str, ...], str] | None:
    try:
        out = subprocess.check_output(
            ["fc-match", "-f", "%{family}\n%{file}\n", pattern],
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    if not out:
        return None
    lines = out.splitlines()
    if len(lines) < 2:
        return None
    family_line, path_line = lines[0].strip(), lines[-1].strip()
    path = Path(path_line)
    if not path.exists():
        return None
    families = tuple(part.strip() for part in family_line.split(",") if part.strip())
    return families, str(path)


def _font_path(pattern: str) -> str | None:
    match = _font_match(pattern)
    if match is None:
        return None
    families, path = match
    requested_family = _requested_family(pattern)
    if requested_family and not any(
        _family_name_matches(requested_family, family) for family in families
    ):
        return None
    return path


def _load_font(patterns: Sequence[str], *, size: int) -> ImageFont.ImageFont:
    for pattern in patterns:
        path = _font_path(pattern)
        if path is None:
            continue
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=8)
def _load_footer_logo(height: int) -> Image.Image | None:
    if height < 1:
        return None
    path = KaraokeVideoRenderer._FOOTER_LOGO_PATH
    if not path.exists():
        return None
    try:
        with Image.open(path) as source:
            logo = source.convert("RGBA")
    except OSError:
        return None
    width = max(1, int(round((logo.width / logo.height) * height)))
    return logo.resize((width, height), Image.LANCZOS)
