"""Intermediate data classes used across the ingest pipeline.

These are plain dataclasses (not Pydantic) because they only live in-process
between the parser and the repository upsert layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class ParsedArticle:
    article_number: str          # "제1조" (normalized)
    article_seq: int             # 1, 2, ... (또는 조의2 → 12 식으로 확장 가능)
    chapter_number: int | None
    chapter_title: str | None
    title: str                   # "(목적)"
    body: str                    # 본문 전체 (항·호 포함)
    category: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    referenced_articles: list[str] = field(default_factory=list)
    referenced_laws: list[str] = field(default_factory=list)


@dataclass
class ParsedRegulation:
    version: int
    effective_date: date
    summary: str | None
    articles: list[ParsedArticle]


@dataclass
class ParsedRevisionEntry:
    article_number: str
    change_type: str             # 'added' | 'modified' | 'removed'
    old_body: str | None
    new_body: str | None
    reason: str | None
    title: str | None = None


@dataclass
class ParsedRegulationDiff:
    from_version: int
    to_version: int
    effective_date: date
    summary: str | None
    entries: list[ParsedRevisionEntry]


@dataclass
class ParsedDecision:
    agenda_seq: int
    topic: str
    category: list[str]
    decision: str
    result: str | None            # '가결' | '부결' | '보류'
    vote_for: int | None
    vote_against: int | None
    vote_abstain: int | None
    related_articles: list[str]
    follow_up: str | None


@dataclass
class ParsedMeeting:
    meeting_date: date
    meeting_type: str              # '정기' | '임시'
    attendees_count: int | None
    quorum: int | None
    raw_text: str
    decisions: list[ParsedDecision]
