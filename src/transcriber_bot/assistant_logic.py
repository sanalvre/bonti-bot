from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "do",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "like",
    "me",
    "more",
    "my",
    "not",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "to",
    "up",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
}


@dataclass(frozen=True)
class KnowledgeDocument:
    source: str
    content: str
    created_at: datetime
    channel_name: str
    author_name: str
    message_id: Optional[int] = None


@dataclass(frozen=True)
class SearchResult:
    document: KnowledgeDocument
    score: float
    snippet: str


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z0-9']+", text.lower())
        if len(token) > 1 and token not in STOPWORDS
    ]


def split_sentences(text: str) -> list[str]:
    normalized = text.replace("\r", "\n")
    chunks = re.split(r"(?<=[.!?])\s+|\n+", normalized)
    return [normalize_whitespace(chunk) for chunk in chunks if normalize_whitespace(chunk)]


def extract_themes(documents: Iterable[KnowledgeDocument], limit: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for document in documents:
        counter.update(tokenize(document.content))
    return [token for token, _ in counter.most_common(limit)]


def search_documents(query: str, documents: Iterable[KnowledgeDocument], limit: int = 8) -> list[SearchResult]:
    normalized_query = normalize_whitespace(query).lower()
    query_tokens = tokenize(query)
    results: list[SearchResult] = []

    for document in documents:
        text = document.content
        lowered = text.lower()
        doc_tokens = tokenize(text)
        if not doc_tokens:
            continue

        overlap = len(set(query_tokens) & set(doc_tokens))
        phrase_bonus = 5 if normalized_query and normalized_query in lowered else 0
        frequency_bonus = sum(doc_tokens.count(token) for token in query_tokens)
        score = float(overlap * 3 + frequency_bonus + phrase_bonus)
        if score <= 0:
            continue

        results.append(SearchResult(document=document, score=score, snippet=make_snippet(text, query_tokens)))

    results.sort(key=lambda item: (item.score, item.document.created_at.timestamp()), reverse=True)
    return results[:limit]


def summarize_documents(
    documents: Iterable[KnowledgeDocument],
    *,
    max_sentences: int = 5,
    query: Optional[str] = None,
) -> str:
    docs = list(documents)
    if not docs:
        return "No matching notes or messages were found."

    frequencies = Counter()
    for document in docs:
        frequencies.update(tokenize(document.content))

    query_tokens = tokenize(query or "")
    candidates: list[tuple[float, int, int, str]] = []
    seen_sentences: set[str] = set()

    for doc_index, document in enumerate(docs):
        for sentence_index, sentence in enumerate(split_sentences(document.content)):
            lowered = sentence.lower()
            if len(sentence) < 25:
                continue
            sentence_tokens = tokenize(sentence)
            if not sentence_tokens:
                continue
            key = lowered.strip()
            if key in seen_sentences:
                continue
            seen_sentences.add(key)

            score = sum(frequencies[token] for token in set(sentence_tokens)) / max(1, len(sentence_tokens))
            if query_tokens:
                overlap = len(set(query_tokens) & set(sentence_tokens))
                score += overlap * 2.5
                if normalize_whitespace(query or "").lower() in lowered:
                    score += 4

            candidates.append((score, doc_index, sentence_index, sentence))

    if not candidates:
        return "No strong summary candidates were found."

    chosen = sorted(candidates, key=lambda item: item[0], reverse=True)[:max_sentences]
    chosen.sort(key=lambda item: (item[1], item[2]))
    return "\n".join(f"- {item[3]}" for item in chosen)


def answer_question(question: str, documents: Iterable[KnowledgeDocument]) -> tuple[str, list[SearchResult]]:
    results = search_documents(question, documents, limit=5)
    if not results:
        return (
            "I couldn't find a strong answer in the messages, notes, or transcript files I scanned.",
            [],
        )

    answer_summary = summarize_documents(
        [result.document for result in results],
        max_sentences=3,
        query=question,
    )
    return answer_summary, results


def make_snippet(text: str, query_tokens: Iterable[str], max_length: int = 180) -> str:
    cleaned = normalize_whitespace(text)
    if len(cleaned) <= max_length:
        return cleaned

    lowered = cleaned.lower()
    for token in query_tokens:
        index = lowered.find(token.lower())
        if index >= 0:
            start = max(0, index - max_length // 3)
            end = min(len(cleaned), start + max_length)
            snippet = cleaned[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(cleaned):
                snippet += "..."
            return snippet

    return cleaned[: max_length - 3].rstrip() + "..."
