from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np
import torch
from ctc_forced_aligner import (
    forced_align as ctc_forced_align,
    generate_emissions,
    get_spans,
    load_alignment_model,
    load_audio,
    merge_repeats,
    postprocess_results,
    preprocess_text,
)

from ai_karaoke.services.karaoke_file_service import clean_lyrics_lines

DEFAULT_MODEL = "MahmoudAshraf/mms-300m-1130-forced-aligner"
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


@dataclass
class AlignmentConfig:
    language: str = "auto"
    romanize: bool = False
    split_size: str = "word"
    star_frequency: str = "segment"
    merge_threshold: float = 0.0
    window_length: int = 30
    context_length: int = 2
    batch_size: int = 4
    model_path: str = DEFAULT_MODEL
    attn_implementation: str | None = None
    device: str | None = None
    compute_dtype: torch.dtype | None = None


class LyricsAligner:
    def __init__(self, config: AlignmentConfig | None = None) -> None:
        self.config = config or AlignmentConfig()
        device = self.config.device or (
            "cuda" if torch.cuda.is_available() else "cpu"
        )
        if self.config.compute_dtype is None:
            dtype = torch.float16 if device == "cuda" else torch.float32
        else:
            dtype = self.config.compute_dtype

        self.model, self.tokenizer = load_alignment_model(
            device,
            self.config.model_path,
            self.config.attn_implementation,
            dtype,
        )

    def align_word_segments(self, audio_path: Path, text: str) -> List[dict]:
        language, romanize = self._resolve_language(text)
        audio_waveform = load_audio(str(audio_path), self.model.dtype, self.model.device)
        emissions, stride = generate_emissions(
            self.model,
            audio_waveform,
            self.config.window_length,
            self.config.context_length,
            self.config.batch_size,
        )
        tokens_starred, text_starred = preprocess_text(
            text,
            romanize,
            language,
            self.config.split_size,
            self.config.star_frequency,
        )
        dictionary = self._build_alignment_dictionary(emissions.shape[-1])
        tokens_starred = self._sanitize_tokens(tokens_starred, dictionary)
        segments, scores, blank_token = self._get_alignments_safe(
            emissions, tokens_starred, dictionary
        )
        spans = get_spans(tokens_starred, segments, blank_token)
        return postprocess_results(
            text_starred,
            spans,
            stride,
            scores,
            self.config.merge_threshold,
        )

    def _resolve_language(self, text: str) -> tuple[str, bool]:
        language = self.config.language
        romanize = self.config.romanize
        if language == "auto":
            if CYRILLIC_RE.search(text):
                return "rus", True
            return "eng", romanize
        return language, romanize

    def _get_alignments_safe(
        self,
        emissions: torch.Tensor,
        tokens_starred: list[str],
        dictionary: dict[str, int],
    ) -> tuple[list, np.ndarray, str]:
        vocab_size = emissions.shape[-1]
        token_indices = [
            dictionary[c]
            for c in " ".join(tokens_starred).split(" ")
            if c in dictionary
        ]
        if not token_indices:
            raise ValueError("No valid tokens after normalization.")

        blank_id = dictionary.get("<blank>", self.tokenizer.pad_token_id or 0)
        if blank_id >= vocab_size:
            blank_id = 0

        if not emissions.is_cpu:
            emissions = emissions.cpu()

        targets = np.asarray([token_indices], dtype=np.int64)
        path, scores = ctc_forced_align(
            emissions.unsqueeze(0).float().numpy(),
            targets,
            blank=blank_id,
        )
        path = path.squeeze().tolist()

        idx_to_token_map = {v: k for k, v in dictionary.items()}
        segments = merge_repeats(path, idx_to_token_map)
        return segments, scores, idx_to_token_map[blank_id]

    def _build_alignment_dictionary(self, vocab_size: int) -> dict[str, int]:
        star_id = vocab_size - 1
        raw_vocab = self.tokenizer.get_vocab()
        dictionary = {
            k.lower(): v for k, v in raw_vocab.items() if v < star_id
        }
        dictionary["<star>"] = star_id
        return dictionary

    def _sanitize_tokens(
        self, tokens_starred: list[str], dictionary: dict[str, int]
    ) -> list[str]:
        sanitized: list[str] = []
        for token in tokens_starred:
            if token == "<star>":
                sanitized.append(token)
                continue
            chars = [ch for ch in token.split(" ") if ch in dictionary]
            sanitized.append(" ".join(chars))
        return sanitized
def build_karaoke_entries(lines: List[str], word_segments: List[dict]) -> List[dict]:
    entries: List[dict] = []
    word_idx = 0
    prev_end = 0.0
    total_words = len(word_segments)

    for line in lines:
        line_words = line.split()
        words: List[dict] = []
        line_start = prev_end
        line_end = prev_end

        for token in line_words:
            if word_idx >= total_words:
                break

            segment = word_segments[word_idx]
            word_idx += 1

            try:
                start_ts = float(segment.get("start", line_end))
            except (AttributeError, TypeError, ValueError):
                start_ts = line_end
            try:
                end_ts = float(segment.get("end", start_ts))
            except (AttributeError, TypeError, ValueError):
                end_ts = start_ts

            if start_ts < line_end:
                start_ts = line_end
            if end_ts < start_ts:
                end_ts = start_ts

            words.append(
                {
                    "word": token,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                }
            )
            if len(words) == 1:
                line_start = start_ts
            line_end = end_ts

        # Keep all original words in the JSON even when alignment returned fewer segments.
        if len(words) < len(line_words):
            filler_ts = line_end
            for token in line_words[len(words):]:
                words.append(
                    {
                        "word": token,
                        "start_ts": filler_ts,
                        "end_ts": filler_ts,
                    }
                )

        if line_end < prev_end:
            line_end = prev_end
        if line_start < prev_end:
            line_start = prev_end

        entries.append(
            {
                "line": line,
                "start_ts": line_start,
                "end_ts": line_end,
                "words": words,
            }
        )
        prev_end = line_end

    return entries
