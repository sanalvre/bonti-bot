from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.transcriber_bot.state import BotState


class BotStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "state.sqlite3"
        self.state = BotState(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_enable_and_disable_channel(self) -> None:
        self.state.set_channel_enabled(123, True)
        self.assertTrue(self.state.is_channel_enabled(123))
        self.state.set_channel_enabled(123, False)
        self.assertFalse(self.state.is_channel_enabled(123))

    def test_add_search_and_mark_ideas(self) -> None:
        created = self.state.add_idea(99, "business", "Launch a private note bot.", "open")
        self.assertEqual(created.category, "business")

        found = self.state.search_ideas(99, category="business")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].idea_id, created.idea_id)

        updated = self.state.mark_idea(created.idea_id, 99, "starred")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.marker, "starred")

    def test_due_reminders_round_trip(self) -> None:
        due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        reminder = self.state.add_reminder(99, 777, "Check the voice transcript.", due_at)

        due = self.state.get_due_reminders(datetime.now(timezone.utc))
        self.assertEqual([item.reminder_id for item in due], [reminder.reminder_id])

        self.state.mark_reminder_sent(reminder.reminder_id)
        due_after_send = self.state.get_due_reminders(datetime.now(timezone.utc))
        self.assertEqual(due_after_send, [])

