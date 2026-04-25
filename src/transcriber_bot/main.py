from __future__ import annotations

import os

from .bot import TranscriberBot
from .config import load_config
from .logging_utils import configure_logging
from .state import BotState
from .transcription import Transcriber


def main() -> None:
    config = load_config()
    if config.hf_home is not None:
        config.hf_home.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(config.hf_home)
    configure_logging(config.log_level)
    state = BotState(config.db_path)
    transcriber = Transcriber(config)
    bot = TranscriberBot(config, state, transcriber)
    bot.run(config.discord_bot_token, log_handler=None)
