"""Regression tests for the regulation markdown parser.

Pins: chapter/article boundary detection, meta extraction, article count,
title normalization, body accumulation (including 항·호 bullets).
"""
from __future__ import annotations

from datetime import date

from apt_domain_mcp.ingest.parser_regulation import parse_regulation_markdown


def test_regulation_v1_meta(regulation_v1_path):
    reg = parse_regulation_markdown(regulation_v1_path)
    assert reg.version == 1
    assert reg.effective_date == date(2018, 10, 1)


def test_regulation_v1_article_count(regulation_v1_path):
    """v1 has exactly 84 조 (Phase 1 synthetic spec)."""
    reg = parse_regulation_markdown(regulation_v1_path)
    assert len(reg.articles) == 84


def test_regulation_v1_article_numbers_are_sequential(regulation_v1_path):
    reg = parse_regulation_markdown(regulation_v1_path)
    numbers = [a.article_number for a in reg.articles]
    expected = [f"제{i}조" for i in range(1, 85)]
    assert numbers == expected


def test_regulation_v1_first_article(regulation_v1_path):
    reg = parse_regulation_markdown(regulation_v1_path)
    first = reg.articles[0]
    assert first.article_number == "제1조"
    assert first.title == "(목적)"
    assert first.chapter_number == 1
    assert first.chapter_title  # non-empty
    assert first.body.strip() != ""


def test_regulation_v1_titles_are_parenthesized(regulation_v1_path):
    reg = parse_regulation_markdown(regulation_v1_path)
    for a in reg.articles:
        assert a.title.startswith("(") and a.title.endswith(")"), (
            f"{a.article_number} title not parenthesized: {a.title!r}"
        )


def test_regulation_v1_chapter_assignment(regulation_v1_path):
    """Every article must be assigned to a chapter (no orphan)."""
    reg = parse_regulation_markdown(regulation_v1_path)
    for a in reg.articles:
        assert a.chapter_number is not None, f"{a.article_number} has no chapter"
        assert a.chapter_title is not None


def test_regulation_v1_article_bodies_nonempty(regulation_v1_path):
    reg = parse_regulation_markdown(regulation_v1_path)
    empties = [a.article_number for a in reg.articles if not a.body.strip()]
    assert not empties, f"empty bodies: {empties}"


def test_regulation_v1_article_seq_strictly_increasing(regulation_v1_path):
    reg = parse_regulation_markdown(regulation_v1_path)
    seqs = [a.article_seq for a in reg.articles]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_regulation_v1_body_retains_hang_ho_bullets(regulation_v1_path):
    """Article body accumulation must keep 1./2./3. (항·호) bullets intact —
    a flush bug would truncate bodies to the first paragraph."""
    reg = parse_regulation_markdown(regulation_v1_path)
    # Find any article whose body contains "1." bullets, should be common.
    bulleted = [a for a in reg.articles if "\n1." in a.body or a.body.lstrip().startswith("1.")]
    assert bulleted, "no bulleted bodies found — parser may be swallowing list items"
