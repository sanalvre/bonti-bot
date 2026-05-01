from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.transcriber_bot.config import load_config


class ConfigTests(unittest.TestCase):
    def test_large_model_request_is_forced_to_medium(self) -> None:
        env = {
            "DISCORD_BOT_TOKEN": "token",
            "TRANSCRIBE_MODEL": "large-v3-turbo",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_config()

        self.assertEqual(config.model_name, "medium")
