"""Regression tests for the meeting markdown parser.

Pins: header meta (date/type/attendees/quorum), agenda boundary, decision
result classification (가결/보류; synthetic set currently has no 부결),
vote tuple extraction, related_articles regex, follow_up capture.
"""
from __future__ import annotations

from datetime import date

from apt_domain_mcp.ingest.parser_meeting import parse_meeting_markdown


def test_meeting_2023_11_header_and_counts(meetings_dir):
    m = parse_meeting_markdown(meetings_dir / "2023-11-20_regular.md")
    assert m.meeting_date == date(2023, 11, 20)
    assert m.meeting_type == "정기"
    assert m.attendees_count == 19
    assert m.quorum == 10
    assert len(m.decisions) == 3
    # raw_text must be preserved verbatim for downstream embedding / audit
    assert m.raw_text.strip() != ""


def test_meeting_2023_11_decision_results(meetings_dir):
    m = parse_meeting_markdown(meetings_dir / "2023-11-20_regular.md")
    results = [d.result for d in m.decisions]
    assert results == ["가결", "보류", "가결"]


def test_meeting_2023_11_vote_parsing(meetings_dir):
    m = parse_meeting_markdown(meetings_dir / "2023-11-20_regular.md")
    d1 = m.decisions[0]
    assert d1.vote_for == 17
    assert d1.vote_against == 0
    assert d1.vote_abstain == 2
    # vote_for + against + abstain should equal attendees_count in this fixture
    assert d1.vote_for + d1.vote_against + d1.vote_abstain == m.attendees_count


def test_meeting_2023_11_related_articles(meetings_dir):
    m = parse_meeting_markdown(meetings_dir / "2023-11-20_regular.md")
    rel = m.decisions[0].related_articles
    # Pinning exact set: synthetic spec
    assert set(rel) >= {"제21조제1호", "제45조", "제47조"}


def test_meeting_2023_11_follow_up_present(meetings_dir):
    m = parse_meeting_markdown(meetings_dir / "2023-11-20_regular.md")
    for d in m.decisions:
        assert d.follow_up, f"agenda {d.agenda_seq} missing follow_up"


def test_meeting_extraordinary_type_and_seq(meetings_dir):
    m = parse_meeting_markdown(meetings_dir / "2024-06-10_extraordinary.md")
    assert m.meeting_type == "임시"
    assert len(m.decisions) == 2
    # agenda_seq monotonic starting at 1
    assert [d.agenda_seq for d in m.decisions] == [1, 2]


def test_meeting_2025_05_references_v3_new_article_86(meetings_dir):
    """The 2025-05-30 extraordinary meeting is the first real-world use of
    제86조 (층간소음, added in v3). This pins that the related_articles regex
    correctly picks up 제86조 — a regression here would mean the parser
    swallowed 2-digit or new article numbers."""
    m = parse_meeting_markdown(meetings_dir / "2025-05-30_extraordinary.md")
    related_first = m.decisions[0].related_articles
    assert "제86조" in related_first


def test_all_meetings_parse_and_aggregate_to_16_decisions(meetings_dir):
    """Phase 1 ground truth: 6 meetings, 16 decisions total."""
    files = sorted(meetings_dir.glob("*.md"))
    assert len(files) == 6
    total = 0
    for p in files:
        m = parse_meeting_markdown(p)
        assert m.meeting_date is not None
        assert m.meeting_type in ("정기", "임시")
        assert m.decisions, f"{p.name} has zero decisions"
        for d in m.decisions:
            assert d.result in ("가결", "부결", "보류"), (
                f"{p.name} agenda {d.agenda_seq} unknown result {d.result!r}"
            )
            assert d.decision.strip() != ""
        total += len(m.decisions)
    assert total == 16


def test_related_articles_regex_does_not_leak_prose(meetings_dir):
    """Related articles list should contain only 제N조(제M호)? tokens, never
    stray Korean prose."""
    import re

    token = re.compile(r"^제\d+조(?:제\d+호)?(?:의\d+)?$")
    for p in sorted(meetings_dir.glob("*.md")):
        m = parse_meeting_markdown(p)
        for d in m.decisions:
            for art in d.related_articles:
                assert token.match(art), f"{p.name} agenda {d.agenda_seq}: bad token {art!r}"
