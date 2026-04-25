from __future__ import annotations

import asyncio
import io
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel

from .config import AppConfig

LOGGER = logging.getLogger("transcriber_bot.transcription")


@dataclass
class TranscriptResult:
    text: str
    language: Optional[str]


class TranscriptionError(RuntimeError):
    """Raised when audio cannot be transcribed safely."""


class Transcriber:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._model: Optional[WhisperModel] = None
        self._loaded_model_name: Optional[str] = None
        self._model_lock = asyncio.Lock()

    async def transcribe_bytes(self, audio_bytes: bytes) -> TranscriptResult:
        model = await self._get_model()
        pcm_audio = await asyncio.to_thread(self._decode_audio_to_float32, audio_bytes)
        segments, info = await asyncio.to_thread(
            model.transcribe,
            pcm_audio,
            beam_size=self.config.beam_size,
            vad_filter=self.config.vad_filter,
        )
        text = self._format_segments(segments)
        if not text:
            raise TranscriptionError("No speech was detected in the provided voice message.")
        return TranscriptResult(text=text, language=getattr(info, "language", None))

    def build_transcript_file(self, message_id: int, transcript_text: str) -> io.BytesIO:
        payload = transcript_text.encode("utf-8")
        buffer = io.BytesIO(payload)
        buffer.name = f"transcript-{message_id}.txt"
        buffer.seek(0)
        return buffer

    async def _get_model(self) -> WhisperModel:
        if self._model is not None:
            return self._model

        async with self._model_lock:
            if self._model is None:
                candidates = [self.config.model_name]
                candidates.extend(
                    candidate
                    for candidate in self.config.model_fallbacks
                    if candidate and candidate != self.config.model_name
                )
                last_error: Optional[Exception] = None
                for model_name in candidates:
                    try:
                        LOGGER.info(
                            "loading_whisper_model model=%s compute_type=%s",
                            model_name,
                            self.config.compute_type,
                        )
                        self._model = await asyncio.to_thread(
                            WhisperModel,
                            model_name,
                            device="cpu",
                            compute_type=self.config.compute_type,
                            download_root=str(self.config.hf_home) if self.config.hf_home is not None else None,
                        )
                        self._loaded_model_name = model_name
                        if model_name != self.config.model_name:
                            LOGGER.warning(
                                "whisper_model_fallback_active requested=%s active=%s",
                                self.config.model_name,
                                model_name,
                            )
                        break
                    except Exception as exc:
                        last_error = exc
                        LOGGER.warning("whisper_model_load_failed model=%s", model_name, exc_info=True)

                if self._model is None:
                    raise TranscriptionError(
                        "The transcription model could not be loaded on this host. "
                        "Try a lighter model like `medium` or `small`."
                    ) from last_error
        return self._model

    def _decode_audio_to_float32(self, audio_bytes: bytes) -> np.ndarray:
        command = [
            self.config.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "pipe:1",
        ]
        try:
            completed = subprocess.run(
                command,
                input=audio_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except FileNotFoundError as exc:
            raise TranscriptionError(
                "FFmpeg is not installed or not available on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            error_text = exc.stderr.decode("utf-8", errors="ignore").strip()
            raise TranscriptionError(
                f"FFmpeg failed to decode the voice message. {error_text or 'No extra details.'}"
            ) from exc

        pcm_bytes = completed.stdout
        if not pcm_bytes:
            raise TranscriptionError("Decoded audio was empty.")

        audio = np.frombuffer(pcm_bytes, np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            raise TranscriptionError("Decoded audio contained no samples.")
        return audio

    def _format_segments(self, segments) -> str:
        chunks: list[str] = []
        for segment in segments:
            cleaned = self._clean_segment(segment.text)
            if cleaned:
                chunks.append(cleaned)

        if not chunks:
            return ""

        text = " ".join(chunks)
        text = re.sub(r"\s+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _clean_segment(self, text: str) -> str:
        value = re.sub(r"\s+", " ", text).strip()
        value = self._collapse_immediate_repetition(value)
        return value

    def _collapse_immediate_repetition(self, text: str) -> str:
        words = text.split()
        if len(words) < 6:
            return text

        collapsed: list[str] = []
        for word in words:
            if len(collapsed) >= 2 and collapsed[-1].lower() == word.lower() and collapsed[-2].lower() == word.lower():
                continue
            collapsed.append(word)
        return " ".join(collapsed)
