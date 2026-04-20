"""Regression tests for the regulation diff parser + apply_diff_to_articles.

Critical regressions guarded here:
- 2efbb0c: added articles must carry the diff-header title, not `(신설)`
  placeholder. The bug was that `apply_diff_to_articles` dropped the title
  captured from the `## N. 제XX조 (title) — 신설` header.
- modified/added/removed change_type classification.
- old_body / new_body / reason subsection extraction.
"""
from __future__ import annotations

from datetime import date

from apt_domain_mcp.ingest.parser_regulation import parse_regulation_markdown
from apt_domain_mcp.ingest.parser_regulation_diff import (
    apply_diff_to_articles,
    parse_regulation_diff,
)


def test_v2_diff_meta(regulation_v2_diff_path):
    diff = parse_regulation_diff(regulation_v2_diff_path)
    assert diff.from_version == 1
    assert diff.to_version == 2
    assert diff.effective_date == date(2020, 6, 1)


def test_v2_diff_entry_count_and_types(regulation_v2_diff_path):
    diff = parse_regulation_diff(regulation_v2_diff_path)
    assert len(diff.entries) == 3
    by_num = {e.article_number: e for e in diff.entries}
    assert set(by_num.keys()) == {"제41조", "제55조", "제85조"}
    assert by_num["제41조"].change_type == "modified"
    assert by_num["제55조"].change_type == "modified"
    assert by_num["제85조"].change_type == "added"


def test_v2_modified_has_old_new_reason(regulation_v2_diff_path):
    diff = parse_regulation_diff(regulation_v2_diff_path)
    e = next(x for x in diff.entries if x.article_number == "제41조")
    assert e.old_body and e.old_body.strip()
    assert e.new_body and e.new_body.strip()
    assert e.reason and e.reason.strip()
    assert e.old_body != e.new_body


def test_v2_added_entry_has_title_and_new_body(regulation_v2_diff_path):
    """2efbb0c guard — added entry must preserve the title from the `— 신설`
    header so downstream apply_diff_to_articles can use it."""
    diff = parse_regulation_diff(regulation_v2_diff_path)
    added = next(x for x in diff.entries if x.change_type == "added")
    assert added.article_number == "제85조"
    assert added.title and added.title.strip()
    assert "신설" not in added.title  # the literal placeholder must not leak in
    assert added.old_body is None
    assert added.new_body and added.new_body.strip()
    assert added.reason and added.reason.strip()


def test_v3_diff_meta_and_entries(regulation_v3_diff_path):
    diff = parse_regulation_diff(regulation_v3_diff_path)
    assert diff.from_version == 2
    assert diff.to_version == 3
    assert len(diff.entries) == 3
    types = {e.article_number: e.change_type for e in diff.entries}
    assert types == {
        "제13조": "modified",
        "제39조": "modified",
        "제86조": "added",
    }


def test_v3_added_article_is_layered_on_top_of_v2(
    regulation_v1_path, regulation_v2_diff_path, regulation_v3_diff_path
):
    """Full layered application: v1 → +v2 diff → +v3 diff.

    This is the exact call sequence used by seed.py. Regression targets:
    - article count grows: 84 → 85 → 86
    - 제85조 and 제86조 both carry the real title (not `(신설)`)
    - Modified article bodies in v3 pick up the new_body
    """
    v1 = parse_regulation_markdown(regulation_v1_path)
    d2 = parse_regulation_diff(regulation_v2_diff_path)
    d3 = parse_regulation_diff(regulation_v3_diff_path)

    v2_articles = apply_diff_to_articles(v1.articles, d2)
    v3_articles = apply_diff_to_articles(v2_articles, d3)

    assert len(v1.articles) == 84
    assert len(v2_articles) == 85
    assert len(v3_articles) == 86

    by_num_v3 = {a.article_number: a for a in v3_articles}

    a85 = by_num_v3["제85조"]
    a86 = by_num_v3["제86조"]
    assert a85.title != "(신설)", "제85조 title regression: got placeholder"
    assert a86.title != "(신설)", "제86조 title regression: got placeholder"
    assert a85.title.startswith("(") and a85.title.endswith(")")
    assert a86.title.startswith("(") and a86.title.endswith(")")
    # Sanity: real titles should be 반려동물·층간소음 (synthetic spec)
    assert "반려동물" in a85.title
    assert "층간소음" in a86.title

    # Modified article's body reflects the diff's new_body
    a13 = by_num_v3["제13조"]
    d3_13 = next(e for e in d3.entries if e.article_number == "제13조")
    assert a13.body.strip() == (d3_13.new_body or "").strip()


def test_apply_diff_preserves_unchanged_articles(
    regulation_v1_path, regulation_v2_diff_path
):
    v1 = parse_regulation_markdown(regulation_v1_path)
    diff = parse_regulation_diff(regulation_v2_diff_path)
    v2 = apply_diff_to_articles(v1.articles, diff)
    changed = {e.article_number for e in diff.entries if e.change_type != "added"}
    v1_by = {a.article_number: a for a in v1.articles}
    v2_by = {a.article_number: a for a in v2}
    for num, a in v1_by.items():
        if num in changed:
            continue
        assert num in v2_by
        assert v2_by[num].body == a.body, f"{num} body drifted without diff entry"
