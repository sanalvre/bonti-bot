from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class IdeaRecord:
    idea_id: int
    user_id: int
    category: str
    content: str
    marker: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReminderRecord:
    reminder_id: int
    user_id: int
    channel_id: int
    content: str
    due_at: datetime
    created_at: datetime
    sent_at: Optional[datetime]


class BotState:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS enabled_channels (
                    channel_id TEXT PRIMARY KEY,
                    enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ideas (
                    idea_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sent_at TEXT
                )
                """
            )
            connection.commit()

    def set_channel_enabled(self, channel_id: int, enabled: bool) -> None:
        with self._connect() as connection:
            if enabled:
                connection.execute(
                    "INSERT OR REPLACE INTO enabled_channels(channel_id) VALUES (?)",
                    (str(channel_id),),
                )
            else:
                connection.execute(
                    "DELETE FROM enabled_channels WHERE channel_id = ?",
                    (str(channel_id),),
                )
            connection.commit()

    def is_channel_enabled(self, channel_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM enabled_channels WHERE channel_id = ?",
                (str(channel_id),),
            ).fetchone()
        return row is not None

    def add_idea(self, user_id: int, category: str, content: str, marker: str) -> IdeaRecord:
        now = self._iso_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ideas(user_id, category, content, marker, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(user_id), category, content, marker, now, now),
            )
            connection.commit()
            idea_id = int(cursor.lastrowid)
        return self.get_idea(idea_id, user_id)

    def get_idea(self, idea_id: int, user_id: int) -> IdeaRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ideas WHERE idea_id = ? AND user_id = ?",
                (idea_id, str(user_id)),
            ).fetchone()
        if row is None:
            raise LookupError(f"Idea {idea_id} does not exist.")
        return self._idea_from_row(row)

    def mark_idea(self, idea_id: int, user_id: int, marker: str) -> Optional[IdeaRecord]:
        updated_at = self._iso_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ideas
                SET marker = ?, updated_at = ?
                WHERE idea_id = ? AND user_id = ?
                """,
                (marker, updated_at, idea_id, str(user_id)),
            )
            connection.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_idea(idea_id, user_id)

    def search_ideas(
        self,
        user_id: int,
        *,
        category: Optional[str] = None,
        query: Optional[str] = None,
        marker: Optional[str] = None,
        limit: int = 10,
    ) -> list[IdeaRecord]:
        conditions = ["user_id = ?"]
        parameters: list[str | int] = [str(user_id)]

        if category:
            conditions.append("category = ?")
            parameters.append(category)
        if marker:
            conditions.append("marker = ?")
            parameters.append(marker)
        if query:
            conditions.append("LOWER(content) LIKE ?")
            parameters.append(f"%{query.lower()}%")

        sql = (
            "SELECT * FROM ideas WHERE "
            + " AND ".join(conditions)
            + " ORDER BY updated_at DESC, idea_id DESC LIMIT ?"
        )
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [self._idea_from_row(row) for row in rows]

    def add_reminder(self, user_id: int, channel_id: int, content: str, due_at: datetime) -> ReminderRecord:
        created_at = self._iso_now()
        due_at_value = self._to_iso(due_at)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders(user_id, channel_id, content, due_at, created_at, sent_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (str(user_id), str(channel_id), content, due_at_value, created_at),
            )
            connection.commit()
            reminder_id = int(cursor.lastrowid)
        return self.get_reminder(reminder_id)

    def get_reminder(self, reminder_id: int) -> ReminderRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE reminder_id = ?",
                (reminder_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Reminder {reminder_id} does not exist.")
        return self._reminder_from_row(row)

    def get_due_reminders(self, now: datetime) -> list[ReminderRecord]:
        iso_now = self._to_iso(now)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reminders
                WHERE sent_at IS NULL AND due_at <= ?
                ORDER BY due_at ASC, reminder_id ASC
                """,
                (iso_now,),
            ).fetchall()
        return [self._reminder_from_row(row) for row in rows]

    def mark_reminder_sent(self, reminder_id: int, sent_at: Optional[datetime] = None) -> None:
        sent_at_value = self._to_iso(sent_at or datetime.now(timezone.utc))
        with self._connect() as connection:
            connection.execute(
                "UPDATE reminders SET sent_at = ? WHERE reminder_id = ?",
                (sent_at_value, reminder_id),
            )
            connection.commit()

    def _idea_from_row(self, row: sqlite3.Row) -> IdeaRecord:
        return IdeaRecord(
            idea_id=int(row["idea_id"]),
            user_id=int(row["user_id"]),
            category=row["category"],
            content=row["content"],
            marker=row["marker"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _reminder_from_row(self, row: sqlite3.Row) -> ReminderRecord:
        sent_at = row["sent_at"]
        return ReminderRecord(
            reminder_id=int(row["reminder_id"]),
            user_id=int(row["user_id"]),
            channel_id=int(row["channel_id"]),
            content=row["content"],
            due_at=datetime.fromisoformat(row["due_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            sent_at=datetime.fromisoformat(sent_at) if sent_at else None,
        )

    def _iso_now(self) -> str:
        return self._to_iso(datetime.now(timezone.utc))

    def _to_iso(self, value: datetime) -> str:
        normalized = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.isoformat()
