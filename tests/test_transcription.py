from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.transcriber_bot.config import AppConfig
from src.transcriber_bot.transcription import Transcriber, TranscriptionError


class TranscriptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_model_load_falls_back_to_lighter_model(self) -> None:
        config = AppConfig(
            discord_bot_token="token",
            db_path=Path(self.tempdir.name) / "state.sqlite3",
            model_name="large-v3-turbo",
            model_fallbacks=("medium", "small"),
            local_timezone="UTC",
        )
        transcriber = Transcriber(config)
        loaded_models: list[str] = []

        def fake_whisper_model(model_name: str, **_: object) -> object:
            loaded_models.append(model_name)
            if model_name == "large-v3-turbo":
                raise RuntimeError("too heavy")
            return object()

        with patch("src.transcriber_bot.transcription.WhisperModel", side_effect=fake_whisper_model):
            model = await transcriber._get_model()

        self.assertIsNotNone(model)
        self.assertEqual(loaded_models, ["large-v3-turbo", "medium"])

    async def test_model_load_raises_transcription_error_when_all_candidates_fail(self) -> None:
        config = AppConfig(
            discord_bot_token="token",
            db_path=Path(self.tempdir.name) / "state.sqlite3",
            model_name="large-v3-turbo",
            model_fallbacks=("medium",),
            local_timezone="UTC",
        )
        transcriber = Transcriber(config)

        with patch("src.transcriber_bot.transcription.WhisperModel", side_effect=RuntimeError("boom")):
            with self.assertRaises(TranscriptionError):
                await transcriber._get_model()
