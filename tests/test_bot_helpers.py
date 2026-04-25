from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.transcriber_bot.bot import TranscriberBot
from src.transcriber_bot.config import AppConfig
from src.transcriber_bot.state import BotState


class DummyAttachment:
    def __init__(self, *, attachment_id: int, filename: str, content_type: str, payload: bytes, size: int | None = None):
        self.id = attachment_id
        self.filename = filename
        self.content_type = content_type
        self._payload = payload
        self.size = len(payload) if size is None else size
        self.duration = None
        self.waveform = None

    async def read(self) -> bytes:
        return self._payload

    def is_voice_message(self) -> bool:
        return self.filename.endswith(".ogg")


class BotHelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        config = AppConfig(
            discord_bot_token="token",
            db_path=Path(self.tempdir.name) / "state.sqlite3",
            local_timezone="UTC",
        )
        self.state = BotState(config.db_path)
        self.bot = TranscriberBot(config, self.state, transcriber=SimpleNamespace())
        self.bot._connection.user = SimpleNamespace(id=555)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    async def test_message_to_document_reads_text_attachment(self) -> None:
        attachment = DummyAttachment(
            attachment_id=1,
            filename="transcript.txt",
            content_type="text/plain",
            payload=b"Important transcript line about an idea.",
        )
        message = SimpleNamespace(
            author=SimpleNamespace(id=555, bot=True, display_name="Bonti"),
            attachments=[attachment],
            content="Transcription attached as file",
            created_at=SimpleNamespace(),  # placeholder patched below
            channel=SimpleNamespace(name="daily"),
            id=42,
        )
        from datetime import datetime, timezone

        message.created_at = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
        document = await self.bot._message_to_document(message)

        self.assertIsNotNone(document)
        assert document is not None
        self.assertIn("Important transcript line", document.content)

    async def test_message_to_document_skips_plain_bot_status_messages(self) -> None:
        from datetime import datetime, timezone

        message = SimpleNamespace(
            author=SimpleNamespace(id=555, bot=True, display_name="Bonti"),
            attachments=[],
            content="Transcription attached as file",
            created_at=datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc),
            channel=SimpleNamespace(name="daily"),
            id=43,
        )

        document = await self.bot._message_to_document(message)
        self.assertIsNone(document)

    def test_is_supported_voice_message_detects_voice_attachments(self) -> None:
        attachment = DummyAttachment(
            attachment_id=2,
            filename="voice-message.ogg",
            content_type="audio/ogg",
            payload=b"data",
        )
        attachment.duration = 5.0
        message = SimpleNamespace(
            attachments=[attachment],
            flags=SimpleNamespace(voice=False),
            channel=SimpleNamespace(id=777),
            id=888,
        )

        self.assertTrue(self.bot._is_supported_voice_message(message))

    def test_disable_stale_channel_turns_off_channel_state(self) -> None:
        self.state.set_channel_enabled(321, True)

        self.bot._disable_stale_channel(321, reason="unavailable")

        self.assertFalse(self.state.is_channel_enabled(321))

    def test_enabled_channel_ids_round_trip_for_debugging(self) -> None:
        self.state.set_channel_enabled(654, True)

        self.assertEqual(self.state.list_enabled_channel_ids(), [654])

    def test_get_transcription_permission_issue_reports_missing_permissions(self) -> None:
        permissions = SimpleNamespace(
            view_channel=True,
            read_message_history=False,
            send_messages=True,
            attach_files=False,
        )
        channel = SimpleNamespace(
            guild=SimpleNamespace(me=object()),
            permissions_for=lambda _: permissions,
        )

        issue = self.bot._get_transcription_permission_issue(channel)

        self.assertEqual(issue, "missing permissions: Read Message History, Attach Files")
