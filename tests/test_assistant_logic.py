from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.transcriber_bot.assistant_logic import (
    KnowledgeDocument,
    answer_question,
    extract_themes,
    search_documents,
    summarize_documents,
)


def make_doc(content: str, *, channel: str = "ideas", author: str = "user", minute: int = 0) -> KnowledgeDocument:
    return KnowledgeDocument(
        source=f"#{channel}",
        content=content,
        created_at=datetime(2026, 4, 24, 12, minute, tzinfo=timezone.utc),
        channel_name=channel,
        author_name=author,
        message_id=minute + 1,
    )


class AssistantLogicTests(unittest.TestCase):
    def test_search_documents_prefers_relevant_matches(self) -> None:
        documents = [
            make_doc("Business idea for a transcription SaaS with creator analytics."),
            make_doc("Life note about groceries and chores.", minute=1),
            make_doc("Another business concept focused on audio summaries.", minute=2),
        ]

        results = search_documents("business transcription", documents, limit=2)

        self.assertEqual(len(results), 2)
        self.assertIn("transcription", results[0].document.content.lower())

    def test_summarize_documents_extracts_key_sentences(self) -> None:
        documents = [
            make_doc(
                "We should build a lightweight dashboard for notes. "
                "The dashboard should make search, reminders, and voice transcripts easy to review. "
                "A clean summary view matters most."
            ),
            make_doc(
                "Search and reminders are the core workflows. "
                "The bot should stay simple and fast on CPU hosting."
            ),
        ]

        summary = summarize_documents(documents, max_sentences=3)

        self.assertIn("dashboard", summary.lower())
        self.assertIn("search", summary.lower())

    def test_answer_question_returns_evidence(self) -> None:
        documents = [
            make_doc("I want reminders to ping me in the server after four hours."),
            make_doc("Use a personal server to keep all ideas and files together.", minute=1),
        ]

        answer, evidence = answer_question("How should reminders work?", documents)

        self.assertIn("reminders", answer.lower())
        self.assertGreaterEqual(len(evidence), 1)

    def test_extract_themes_returns_top_terms(self) -> None:
        documents = [
            make_doc("Business idea business growth planning."),
            make_doc("Business notes and planning for launch.", minute=1),
        ]

        themes = extract_themes(documents, limit=3)

        self.assertIn("business", themes)
        self.assertIn("planning", themes)

