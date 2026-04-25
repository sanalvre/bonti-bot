from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AppConfig:
    discord_bot_token: str
    model_name: str = "large-v3-turbo"
    model_fallbacks: tuple[str, ...] = ("medium", "small")
    ffmpeg_path: str = "ffmpeg"
    db_path: Path = Path("bot_state.sqlite3")
    max_audio_seconds: int = 240
    max_attachment_mb: int = 25
    global_concurrency: int = 1
    beam_size: int = 5
    compute_type: str = "int8"
    vad_filter: bool = True
    log_level: str = "INFO"
    hf_home: Optional[Path] = None
    reminder_poll_seconds: int = 30
    message_poll_seconds: int = 15
    search_history_limit_per_channel: int = 600
    max_text_attachment_kb: int = 256
    local_timezone: str = "America/Los_Angeles"


def _read_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return int(raw_value)


def _read_model_fallbacks() -> tuple[str, ...]:
    raw_value = os.getenv("TRANSCRIBE_MODEL_FALLBACKS", "medium,small")
    values = [item.strip() for item in raw_value.split(",")]
    return tuple(item for item in values if item)


def load_config() -> AppConfig:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN must be set before starting the bot.")

    return AppConfig(
        discord_bot_token=token,
        model_name=os.getenv("TRANSCRIBE_MODEL", "large-v3-turbo").strip() or "large-v3-turbo",
        model_fallbacks=_read_model_fallbacks(),
        ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg",
        db_path=Path(os.getenv("BOT_DB_PATH", "bot_state.sqlite3")),
        max_audio_seconds=_read_int("MAX_AUDIO_SECONDS", 240),
        max_attachment_mb=_read_int("MAX_ATTACHMENT_MB", 25),
        global_concurrency=max(1, _read_int("GLOBAL_CONCURRENCY", 1)),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        hf_home=Path(os.getenv("HF_HOME")).expanduser() if os.getenv("HF_HOME") else None,
        reminder_poll_seconds=max(5, _read_int("REMINDER_POLL_SECONDS", 30)),
        message_poll_seconds=max(5, _read_int("MESSAGE_POLL_SECONDS", 15)),
        search_history_limit_per_channel=max(50, _read_int("SEARCH_HISTORY_LIMIT_PER_CHANNEL", 600)),
        max_text_attachment_kb=max(16, _read_int("MAX_TEXT_ATTACHMENT_KB", 256)),
        local_timezone=os.getenv("LOCAL_TIMEZONE", "America/Los_Angeles").strip() or "America/Los_Angeles",
    )
