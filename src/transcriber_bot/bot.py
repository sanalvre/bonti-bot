from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands

from .assistant_logic import (
    KnowledgeDocument,
    answer_question,
    extract_themes,
    search_documents,
    summarize_documents,
)
from .config import AppConfig
from .state import BotState, IdeaRecord, ReminderRecord
from .transcription import Transcriber, TranscriptionError

LOGGER = logging.getLogger("transcriber_bot")
VALID_IDEA_MARKERS = {"open", "starred", "parked", "done"}
TEXT_ATTACHMENT_EXTENSIONS = (".txt", ".md", ".log", ".csv", ".json")


class TranscriberBot(discord.Client):
    def __init__(self, config: AppConfig, state: BotState, transcriber: Transcriber) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True

        super().__init__(intents=intents)
        self.config = config
        self.state = state
        self.transcriber = transcriber
        self.tree = app_commands.CommandTree(self)
        self.global_semaphore = asyncio.Semaphore(config.global_concurrency)
        self.channel_locks: dict[int, asyncio.Lock] = {}
        self._processed_messages: OrderedDict[int, None] = OrderedDict()
        self._processed_limit = 2048
        self._attachment_text_cache: OrderedDict[int, str] = OrderedDict()
        self._attachment_text_cache_limit = 256
        self._message_poll_task: Optional[asyncio.Task[None]] = None
        try:
            self._timezone = ZoneInfo(config.local_timezone)
        except ZoneInfoNotFoundError:
            LOGGER.warning("timezone_not_found key=%s; falling back to UTC", config.local_timezone)
            self._timezone = timezone.utc
        self._reminder_task: Optional[asyncio.Task[None]] = None

    async def setup_hook(self) -> None:
        self._register_commands()
        await self.tree.sync()
        self._reminder_task = asyncio.create_task(self._reminder_loop(), name="reminder-loop")
        self._message_poll_task = asyncio.create_task(self._message_poll_loop(), name="message-poll-loop")
        LOGGER.info("slash_commands_synced")

    async def close(self) -> None:
        if self._reminder_task is not None:
            self._reminder_task.cancel()
            try:
                await self._reminder_task
            except asyncio.CancelledError:
                pass
        if self._message_poll_task is not None:
            self._message_poll_task.cancel()
            try:
                await self._message_poll_task
            except asyncio.CancelledError:
                pass
        await super().close()

    def _register_commands(self) -> None:
        transcribe_group = app_commands.Group(
            name="transcribe",
            description="Manage automatic voice-message transcription in this channel.",
        )

        idea_group = app_commands.Group(
            name="idea",
            description="Save and organize ideas by category.",
        )

        async def ensure_guild_channel(
            interaction: discord.Interaction,
        ) -> Optional[discord.abc.GuildChannel | discord.Thread]:
            if interaction.guild is None or interaction.channel is None:
                await self._respond_ephemeral(interaction, "This command only works inside a server channel.")
                return None
            return interaction.channel

        @transcribe_group.command(name="on", description="Enable automatic transcription for this channel.")
        async def transcribe_on(interaction: discord.Interaction) -> None:
            channel = await ensure_guild_channel(interaction)
            if channel is None:
                return
            self.state.set_channel_enabled(channel.id, True)
            LOGGER.info(
                "transcribe_channel_enabled channel_id=%s channel_type=%s guild_id=%s channel_name=%s",
                channel.id,
                type(channel).__name__,
                interaction.guild.id if interaction.guild else None,
                getattr(channel, "name", None),
            )
            await self._respond_ephemeral(
                interaction,
                f"Auto-transcription is now enabled in this channel.\nChannel ID: `{channel.id}`",
            )

        @transcribe_group.command(name="off", description="Disable automatic transcription for this channel.")
        async def transcribe_off(interaction: discord.Interaction) -> None:
            channel = await ensure_guild_channel(interaction)
            if channel is None:
                return
            self.state.set_channel_enabled(channel.id, False)
            LOGGER.info(
                "transcribe_channel_disabled channel_id=%s channel_type=%s guild_id=%s channel_name=%s",
                channel.id,
                type(channel).__name__,
                interaction.guild.id if interaction.guild else None,
                getattr(channel, "name", None),
            )
            await self._respond_ephemeral(
                interaction,
                f"Auto-transcription is now disabled in this channel.\nChannel ID: `{channel.id}`",
            )

        @transcribe_group.command(name="status", description="Show whether automatic transcription is enabled here.")
        async def transcribe_status(interaction: discord.Interaction) -> None:
            channel = await ensure_guild_channel(interaction)
            if channel is None:
                return
            enabled = self.state.is_channel_enabled(channel.id)
            status_text = "enabled" if enabled else "disabled"
            await self._respond_ephemeral(
                interaction,
                f"Auto-transcription is currently {status_text} in this channel.\nChannel ID: `{channel.id}`",
            )

        @idea_group.command(name="add", description="Save a tagged idea.")
        @app_commands.describe(
            category="Short bucket like business, life, music, or content",
            content="The idea you want to save",
            marker="Optional flag: open, starred, parked, or done",
        )
        async def idea_add(
            interaction: discord.Interaction,
            category: str,
            content: str,
            marker: Optional[str] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                normalized_category = self._normalize_category(category)
                normalized_marker = self._normalize_marker(marker or "open")
            except ValueError as exc:
                await self._send_followup(interaction, str(exc))
                return
            record = self.state.add_idea(interaction.user.id, normalized_category, content.strip(), normalized_marker)
            await self._send_followup(
                interaction,
                (
                    f"Saved idea `{record.idea_id}` in `{record.category}` "
                    f"with marker `{record.marker}`.\n{record.content}"
                ),
            )

        @idea_group.command(name="search", description="Find saved ideas by category or keyword.")
        @app_commands.describe(
            category="Category to search, such as business or life",
            query="Optional keyword filter inside idea text",
            marker="Optional flag filter: open, starred, parked, or done",
        )
        async def idea_search(
            interaction: discord.Interaction,
            category: str,
            query: Optional[str] = None,
            marker: Optional[str] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                normalized_category = self._normalize_category(category)
                normalized_marker = self._normalize_marker(marker) if marker else None
            except ValueError as exc:
                await self._send_followup(interaction, str(exc))
                return
            ideas = self.state.search_ideas(
                interaction.user.id,
                category=normalized_category,
                query=query.strip() if query else None,
                marker=normalized_marker,
            )
            if not ideas:
                await self._send_followup(
                    interaction,
                    f"No saved ideas matched category `{normalized_category}`.",
                )
                return
            lines = [self._format_idea(record) for record in ideas]
            await self._send_followup(interaction, "\n".join(lines))

        @idea_group.command(name="mark", description="Change the flag on a saved idea.")
        @app_commands.describe(idea_id="The numeric idea id", marker="open, starred, parked, or done")
        async def idea_mark(interaction: discord.Interaction, idea_id: int, marker: str) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                normalized_marker = self._normalize_marker(marker)
            except ValueError as exc:
                await self._send_followup(interaction, str(exc))
                return
            record = self.state.mark_idea(idea_id, interaction.user.id, normalized_marker)
            if record is None:
                await self._send_followup(interaction, f"I couldn't find idea `{idea_id}` to update.")
                return
            await self._send_followup(
                interaction,
                f"Updated idea `{record.idea_id}` to marker `{record.marker}`.\n{record.content}",
            )

        @self.tree.command(name="search", description="Search your server notes, messages, and transcript files.")
        @app_commands.describe(query="Keyword or topic to search for", hours="How far back to scan")
        async def search_command(interaction: discord.Interaction, query: str, hours: Optional[float] = 720.0) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is None:
                await self._send_followup(interaction, "This command only works inside a server.")
                return
            documents = await self._collect_documents(interaction.guild, since=self._hours_ago(hours or 720.0))
            results = search_documents(query, documents, limit=8)
            if not results:
                await self._send_followup(interaction, f"No matches found for `{query}`.")
                return
            lines = [f"Search results for `{query}`:"]
            for index, result in enumerate(results, start=1):
                created = result.document.created_at.astimezone(self._timezone).strftime("%Y-%m-%d %I:%M %p")
                lines.append(
                    (
                        f"{index}. #{result.document.channel_name} | {result.document.author_name} | {created}\n"
                        f"{result.snippet}"
                    )
                )
            await self._send_followup(interaction, "\n\n".join(lines))

        @self.tree.command(name="ask", description="Answer a question using your server notes and transcript files.")
        @app_commands.describe(question="What do you want to know?", hours="How far back to scan")
        async def ask_command(interaction: discord.Interaction, question: str, hours: Optional[float] = 720.0) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is None:
                await self._send_followup(interaction, "This command only works inside a server.")
                return
            documents = await self._collect_documents(interaction.guild, since=self._hours_ago(hours or 720.0))
            answer_text, evidence = answer_question(question, documents)
            lines = [f"Question: {question}", "", "Best answer I could infer:", answer_text]
            if evidence:
                lines.append("")
                lines.append("Evidence:")
                for result in evidence:
                    created = result.document.created_at.astimezone(self._timezone).strftime("%Y-%m-%d %I:%M %p")
                    lines.append(
                        f"- #{result.document.channel_name} | {result.document.author_name} | {created}: {result.snippet}"
                    )
            await self._send_followup(interaction, "\n".join(lines))

        @self.tree.command(name="summarize", description="Summarize your recent notes or a specific topic.")
        @app_commands.describe(
            topic="Optional topic. If present, summarize matching server-wide notes.",
            hours="How far back to scan",
        )
        async def summarize_command(
            interaction: discord.Interaction,
            topic: Optional[str] = None,
            hours: Optional[float] = 24.0,
        ) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is None or interaction.channel is None:
                await self._send_followup(interaction, "This command only works inside a server channel.")
                return

            since = self._hours_ago(hours or 24.0)
            if topic:
                documents = await self._collect_documents(interaction.guild, since=since)
                matches = search_documents(topic, documents, limit=12)
                selected_docs = [match.document for match in matches]
                heading = f"Summary for topic `{topic}`:"
            else:
                selected_docs = await self._collect_documents(
                    interaction.guild,
                    since=since,
                    channels=[interaction.channel],
                )
                heading = f"Summary for #{interaction.channel.name}:"

            if not selected_docs:
                await self._send_followup(interaction, "I couldn't find enough text to summarize.")
                return

            summary = summarize_documents(selected_docs, max_sentences=5, query=topic)
            themes = ", ".join(extract_themes(selected_docs, limit=5)) or "none"
            await self._send_followup(
                interaction,
                f"{heading}\n\n{summary}\n\nThemes: {themes}",
            )

        @self.tree.command(name="daily", description="Summarize what you've added since midnight Pacific time.")
        async def daily_command(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is None:
                await self._send_followup(interaction, "This command only works inside a server.")
                return
            since = self._midnight_local_utc()
            documents = await self._collect_documents(
                interaction.guild,
                since=since,
                author_id=interaction.user.id,
            )
            if not documents:
                await self._send_followup(interaction, "I couldn't find anything from you since midnight Pacific time.")
                return
            summary = summarize_documents(documents, max_sentences=6)
            themes = ", ".join(extract_themes(documents, limit=6)) or "none"
            window_label = since.astimezone(self._timezone).strftime("%Y-%m-%d %I:%M %p")
            await self._send_followup(
                interaction,
                f"Daily summary since {window_label} Pacific:\n\n{summary}\n\nThemes: {themes}",
            )

        @self.tree.command(name="remind", description="Schedule a reminder in hours.")
        @app_commands.describe(content="What should I remind you about?", hours="How many hours from now")
        async def remind_command(interaction: discord.Interaction, content: str, hours: float) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            channel = await ensure_guild_channel(interaction)
            if channel is None:
                return
            if hours <= 0:
                await self._send_followup(interaction, "Hours must be greater than 0.")
                return
            due_at = datetime.now(timezone.utc) + timedelta(hours=hours)
            reminder = self.state.add_reminder(interaction.user.id, channel.id, content.strip(), due_at)
            due_label = reminder.due_at.astimezone(self._timezone).strftime("%Y-%m-%d %I:%M %p")
            await self._send_followup(
                interaction,
                f"Reminder `{reminder.reminder_id}` set for {due_label} Pacific.\n{reminder.content}",
            )

        self.tree.add_command(transcribe_group)
        self.tree.add_command(idea_group)

    async def on_ready(self) -> None:
        LOGGER.info("bot_ready user=%s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if self.user is None:
            return
        if message.author.bot:
            return
        if message.guild is None:
            return
        if not self.state.is_channel_enabled(message.channel.id):
            return
        await self._maybe_schedule_voice_message(message, source="event")

    async def _maybe_schedule_voice_message(self, message: discord.Message, *, source: str) -> None:
        LOGGER.info(
            "message_received source=%s channel_id=%s message_id=%s attachments=%s author_id=%s",
            source,
            message.channel.id,
            message.id,
            len(message.attachments),
            message.author.id,
        )
        if message.id in self._processed_messages:
            LOGGER.info("message_skipped_duplicate channel_id=%s message_id=%s", message.channel.id, message.id)
            return
        if not self._is_supported_voice_message(message):
            LOGGER.info(
                "voice_message_skipped channel_id=%s message_id=%s attachments=%s flags=%s",
                message.channel.id,
                message.id,
                len(message.attachments),
                message.flags.value,
            )
            return

        self._remember_processed(message.id)
        LOGGER.info("voice_message_accepted source=%s channel_id=%s message_id=%s", source, message.channel.id, message.id)
        asyncio.create_task(self._process_voice_message(message))

    def _is_supported_voice_message(self, message: discord.Message) -> bool:
        if len(message.attachments) != 1:
            return False

        attachment = message.attachments[0]
        content_type = attachment.content_type or ""
        filename = (attachment.filename or "").lower()
        is_audio = content_type.startswith("audio/") or filename.endswith((".ogg", ".oga", ".mp3", ".wav", ".m4a", ".webm", ".aac", ".opus"))

        try:
            is_voice_message = attachment.is_voice_message()
        except Exception:
            is_voice_message = False

        has_voice_fields = getattr(attachment, "duration", None) is not None or getattr(attachment, "waveform", None) is not None
        has_voice_flag = bool(getattr(message.flags, "voice", False))
        LOGGER.info(
            "voice_message_check channel_id=%s message_id=%s filename=%s content_type=%s is_audio=%s attachment_voice=%s has_voice_fields=%s has_voice_flag=%s",
            message.channel.id,
            message.id,
            attachment.filename,
            attachment.content_type,
            is_audio,
            is_voice_message,
            has_voice_fields,
            has_voice_flag,
        )
        return is_audio

    async def _process_voice_message(self, message: discord.Message) -> None:
        channel_lock = self.channel_locks.setdefault(message.channel.id, asyncio.Lock())
        LOGGER.info("voice_message_queue_start channel_id=%s message_id=%s", message.channel.id, message.id)
        async with channel_lock:
            async with self.global_semaphore:
                await self._handle_voice_message(message)

    async def _handle_voice_message(self, message: discord.Message) -> None:
        attachment = message.attachments[0]
        duration = getattr(attachment, "duration", None)
        size_limit_bytes = self.config.max_attachment_mb * 1024 * 1024

        if duration is not None and duration > self.config.max_audio_seconds:
            await self._send_failure_reply(
                message,
                f"Voice message is too long. Limit is {self.config.max_audio_seconds} seconds.",
            )
            self._log_transcription_result(message, duration, "duration_limit")
            return

        if attachment.size > size_limit_bytes:
            await self._send_failure_reply(
                message,
                f"Voice message is too large. Limit is {self.config.max_attachment_mb} MB.",
            )
            self._log_transcription_result(message, duration, "size_limit")
            return

        try:
            LOGGER.info("voice_message_download_start channel_id=%s message_id=%s", message.channel.id, message.id)
            audio_bytes = await self._download_attachment_with_retry(attachment)
            LOGGER.info(
                "voice_message_download_complete channel_id=%s message_id=%s bytes=%s",
                message.channel.id,
                message.id,
                len(audio_bytes),
            )
            transcript = await self.transcriber.transcribe_bytes(audio_bytes)
            if not self.state.is_channel_enabled(message.channel.id):
                self._log_transcription_result(message, duration, "channel_disabled_mid_run", language=transcript.language)
                return
            file_buffer = self.transcriber.build_transcript_file(message.id, transcript.text)
            discord_file = discord.File(file_buffer, filename=file_buffer.name)
            await message.reply(
                content="Transcription attached as file",
                file=discord_file,
                mention_author=False,
            )
            self._log_transcription_result(message, duration, "ok", language=transcript.language)
        except TranscriptionError as exc:
            await self._send_failure_reply(message, str(exc))
            self._log_transcription_result(message, duration, "transcription_error")
        except discord.HTTPException:
            LOGGER.exception(
                "discord_http_error channel_id=%s message_id=%s",
                message.channel.id,
                message.id,
            )
        except Exception:
            LOGGER.exception(
                "unexpected_processing_error channel_id=%s message_id=%s",
                message.channel.id,
                message.id,
            )
            await self._send_failure_reply(
                message,
                "The bot hit an unexpected error while transcribing this voice message.",
            )
            self._log_transcription_result(message, duration, "unexpected_error")

    async def _download_attachment_with_retry(self, attachment: discord.Attachment) -> bytes:
        last_error: Optional[BaseException] = None
        for attempt in range(2):
            try:
                return await attachment.read()
            except discord.HTTPException as exc:
                last_error = exc
                LOGGER.warning(
                    "attachment_download_retry attachment_id=%s attempt=%s",
                    attachment.id,
                    attempt + 1,
                )
                if attempt == 0:
                    await asyncio.sleep(1.0)
        raise TranscriptionError("Failed to download the voice message from Discord.") from last_error

    async def _send_failure_reply(self, message: discord.Message, reason: str) -> None:
        try:
            await message.reply(reason, mention_author=False)
        except discord.HTTPException:
            LOGGER.exception(
                "failed_to_send_error_reply channel_id=%s message_id=%s",
                message.channel.id,
                message.id,
            )

    async def _message_poll_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._poll_enabled_channels_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("message_poll_loop_error")
            await asyncio.sleep(self.config.message_poll_seconds)

    async def _poll_enabled_channels_once(self) -> None:
        for channel_id in self.state.list_enabled_channel_ids():
            channel = self.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.fetch_channel(channel_id)
                except discord.HTTPException:
                    self._disable_stale_channel(
                        channel_id,
                        reason="unavailable",
                    )
                    continue

            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                self._disable_stale_channel(
                    channel_id,
                    reason=f"unsupported:{type(channel).__name__}",
                )
                continue

            permissions = channel.permissions_for(channel.guild.me) if channel.guild and channel.guild.me else None
            if permissions is not None and (not permissions.view_channel or not permissions.read_message_history):
                LOGGER.warning(
                    "message_poll_missing_permissions channel_id=%s view_channel=%s read_history=%s",
                    channel_id,
                    permissions.view_channel,
                    permissions.read_message_history,
                )
                continue

            try:
                async for message in channel.history(limit=10):
                    if message.author.bot:
                        continue
                    if message.id in self._processed_messages:
                        continue
                    if not message.attachments:
                        continue
                    await self._maybe_schedule_voice_message(message, source="poll")
            except discord.HTTPException:
                LOGGER.exception("message_poll_history_failed channel_id=%s", channel_id)

    def _disable_stale_channel(self, channel_id: int, *, reason: str) -> None:
        self.state.set_channel_enabled(channel_id, False)
        LOGGER.warning(
            "message_poll_channel_disabled channel_id=%s reason=%s",
            channel_id,
            reason,
        )

    async def _respond_ephemeral(self, interaction: discord.Interaction, text: str) -> None:
        if interaction.response.is_done():
            await self._send_followup(interaction, text)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def _send_followup(self, interaction: discord.Interaction, text: str) -> None:
        if len(text) <= 1900:
            await interaction.followup.send(text, ephemeral=True)
            return

        payload = io.BytesIO(text.encode("utf-8"))
        payload.name = "response.txt"
        await interaction.followup.send(
            "The response was long, so I attached it as a file.",
            file=discord.File(payload, filename=payload.name),
            ephemeral=True,
        )

    def _remember_processed(self, message_id: int) -> None:
        self._processed_messages[message_id] = None
        self._processed_messages.move_to_end(message_id)
        while len(self._processed_messages) > self._processed_limit:
            self._processed_messages.popitem(last=False)

    def _remember_attachment_text(self, attachment_id: int, text: str) -> None:
        self._attachment_text_cache[attachment_id] = text
        self._attachment_text_cache.move_to_end(attachment_id)
        while len(self._attachment_text_cache) > self._attachment_text_cache_limit:
            self._attachment_text_cache.popitem(last=False)

    async def _collect_documents(
        self,
        guild: discord.Guild,
        *,
        since: Optional[datetime] = None,
        author_id: Optional[int] = None,
        channels: Optional[Iterable[discord.abc.GuildChannel | discord.Thread]] = None,
    ) -> list[KnowledgeDocument]:
        documents: list[KnowledgeDocument] = []
        selected_channels = list(channels) if channels is not None else list(guild.text_channels)

        for channel in selected_channels:
            if not hasattr(channel, "history"):
                continue
            try:
                async for message in channel.history(limit=self.config.search_history_limit_per_channel):
                    if since and message.created_at < since:
                        break
                    document = await self._message_to_document(message, author_id=author_id)
                    if document is not None:
                        documents.append(document)
            except discord.Forbidden:
                LOGGER.warning("forbidden_history channel_id=%s", getattr(channel, "id", "unknown"))
            except discord.HTTPException:
                LOGGER.exception("history_fetch_failed channel_id=%s", getattr(channel, "id", "unknown"))
        documents.sort(key=lambda item: item.created_at)
        return documents

    async def _message_to_document(
        self,
        message: discord.Message,
        *,
        author_id: Optional[int] = None,
    ) -> Optional[KnowledgeDocument]:
        attachment_sections: list[str] = []
        for attachment in message.attachments:
            attachment_text = await self._read_attachment_text(attachment)
            if attachment_text:
                attachment_sections.append(f"[Attachment: {attachment.filename}]\n{attachment_text}")

        if author_id is not None:
            is_owner_message = message.author.id == author_id
            is_transcript_reply = bool(
                self.user
                and message.author.id == self.user.id
                and attachment_sections
            )
            if not is_owner_message and not is_transcript_reply:
                return None

        if message.author.bot and not attachment_sections:
            return None

        parts: list[str] = []
        if message.content.strip():
            parts.append(message.content.strip())
        parts.extend(attachment_sections)
        if not parts:
            return None

        channel_name = getattr(message.channel, "name", "unknown")
        return KnowledgeDocument(
            source=f"#{channel_name}",
            content="\n\n".join(parts),
            created_at=message.created_at,
            channel_name=channel_name,
            author_name=message.author.display_name,
            message_id=message.id,
        )

    async def _read_attachment_text(self, attachment: discord.Attachment) -> Optional[str]:
        if not self._is_text_attachment(attachment):
            return None
        if attachment.size > self.config.max_text_attachment_kb * 1024:
            LOGGER.info("attachment_skipped_too_large attachment_id=%s filename=%s", attachment.id, attachment.filename)
            return None
        if attachment.id in self._attachment_text_cache:
            return self._attachment_text_cache[attachment.id]
        try:
            data = await attachment.read()
        except discord.HTTPException:
            LOGGER.exception("attachment_read_failed attachment_id=%s filename=%s", attachment.id, attachment.filename)
            return None

        text = data.decode("utf-8", errors="ignore").strip()
        if not text:
            return None
        self._remember_attachment_text(attachment.id, text)
        return text

    def _is_text_attachment(self, attachment: discord.Attachment) -> bool:
        content_type = (attachment.content_type or "").lower()
        filename = (attachment.filename or "").lower()
        return content_type.startswith("text/") or content_type in {"application/json"} or filename.endswith(TEXT_ATTACHMENT_EXTENSIONS)

    async def _reminder_loop(self) -> None:
        try:
            while True:
                due_reminders = self.state.get_due_reminders(datetime.now(timezone.utc))
                for reminder in due_reminders:
                    await self._deliver_reminder(reminder)
                await asyncio.sleep(self.config.reminder_poll_seconds)
        except asyncio.CancelledError:
            raise

    async def _deliver_reminder(self, reminder: ReminderRecord) -> None:
        mention = f"<@{reminder.user_id}>"
        due_label = reminder.due_at.astimezone(self._timezone).strftime("%Y-%m-%d %I:%M %p")
        message = f"{mention} reminder from {due_label} Pacific:\n{reminder.content}"

        channel = self.get_channel(reminder.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(reminder.channel_id)
            except discord.HTTPException:
                channel = None

        delivered = False
        if channel is not None and hasattr(channel, "send"):
            try:
                await channel.send(message)
                delivered = True
            except discord.HTTPException:
                LOGGER.exception("reminder_channel_send_failed reminder_id=%s", reminder.reminder_id)

        if not delivered:
            user = self.get_user(reminder.user_id)
            if user is None:
                try:
                    user = await self.fetch_user(reminder.user_id)
                except discord.HTTPException:
                    user = None
            if user is not None:
                try:
                    await user.send(message)
                    delivered = True
                except discord.HTTPException:
                    LOGGER.exception("reminder_dm_send_failed reminder_id=%s", reminder.reminder_id)

        if delivered:
            self.state.mark_reminder_sent(reminder.reminder_id)

    def _normalize_category(self, category: str) -> str:
        normalized = re.sub(r"[^a-z0-9_-]+", "-", category.lower()).strip("-_")
        if not normalized:
            raise ValueError("Category must include letters or numbers.")
        return normalized[:32]

    def _normalize_marker(self, marker: Optional[str]) -> str:
        normalized = (marker or "open").strip().lower()
        if normalized not in VALID_IDEA_MARKERS:
            raise ValueError("Marker must be one of: open, starred, parked, done.")
        return normalized

    def _format_idea(self, idea: IdeaRecord) -> str:
        marker_icon = {
            "open": "[open]",
            "starred": "[starred]",
            "parked": "[parked]",
            "done": "[done]",
        }[idea.marker]
        created = idea.created_at.astimezone(self._timezone).strftime("%Y-%m-%d %I:%M %p")
        return f"`{idea.idea_id}` {marker_icon} `{idea.category}` {created}\n{idea.content}"

    def _hours_ago(self, hours: float) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=max(hours, 0.1))

    def _midnight_local_utc(self) -> datetime:
        now_local = datetime.now(self._timezone)
        midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight_local.astimezone(timezone.utc)

    def _log_transcription_result(
        self,
        message: discord.Message,
        duration: Optional[float],
        status: str,
        *,
        language: Optional[str] = None,
    ) -> None:
        LOGGER.info(
            "transcription_result channel_id=%s message_id=%s duration=%s model=%s language=%s status=%s",
            message.channel.id,
            message.id,
            duration,
            self.config.model_name,
            language,
            status,
        )
