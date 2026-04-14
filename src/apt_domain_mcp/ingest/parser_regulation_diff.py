"""Parse regulation diff markdown files (synthetic/regulation_v2_diff.md,
regulation_v3_diff.md).

Expected structure per revision entry:

    ## N. 제XX조 (title) — 개정
    ### 현행 (vA)
    ... old body ...
    ### 개정 (vB)
    ... new body ...
    ### 개정 사유
    reason text

or for new articles:

    ## N. 제XX조 (title) — 신설
    ### 신설 조문 (vB)
    ... new body ...
    ### 신설 사유
    reason text

The "## 개정 요지" summary table and "## N. 부칙" sections are ignored.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .models import ParsedRegulationDiff, ParsedRevisionEntry

TO_VERSION_RE = re.compile(r"v(\d+)\s*→\s*v(\d+)|v(\d+)\s*->\s*v(\d+)")
DIFF_HEADER_RE = re.compile(r"^-\s*\*\*개정\s*버전\*\*:\s*v(\d+)")
EFFECTIVE_RE = re.compile(r"시행일\*?\*?:\s*(\d{4}-\d{2}-\d{2})")
ENTRY_RE = re.compile(r"^##\s+\d+\.\s+(제\d+조(?:의\d+)?)\s*\((.+?)\)\s*—\s*(개정|신설|삭제)")
SUBSECTION_RE = re.compile(r"^###\s+(개정 사유|신설 사유|삭제 사유|신설 조문|현행|개정)")
TITLE_RE = re.compile(r"^#\s+.+?\(v(\d+)\s*→\s*v(\d+)\)")


def parse_regulation_diff(path: Path) -> ParsedRegulationDiff:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    from_version: int | None = None
    to_version: int | None = None
    effective: date | None = None

    entries: list[ParsedRevisionEntry] = []
    cur_entry: dict | None = None
    cur_sub: str | None = None
    buf: list[str] = []

    def flush_sub() -> None:
        nonlocal buf, cur_sub
        if cur_entry is None or cur_sub is None:
            buf = []
            cur_sub = None
            return
        content = "\n".join(buf).strip()
        if cur_sub == "현행":
            cur_entry["old_body"] = content
        elif cur_sub in ("개정", "신설 조문"):
            cur_entry["new_body"] = content
        elif cur_sub in ("개정 사유", "신설 사유", "삭제 사유"):
            cur_entry["reason"] = content
        buf = []
        cur_sub = None

    def flush_entry() -> None:
        nonlocal cur_entry
        flush_sub()
        if cur_entry is not None:
            entries.append(
                ParsedRevisionEntry(
                    article_number=cur_entry["article_number"],
                    change_type=cur_entry["change_type"],
                    old_body=cur_entry.get("old_body"),
                    new_body=cur_entry.get("new_body"),
                    reason=cur_entry.get("reason"),
                )
            )
        cur_entry = None

    for raw in lines:
        line = raw.rstrip()

        # Stop at 부칙 section
        if re.match(r"^##\s+\d+\.\s+부칙", line):
            flush_entry()
            break
        # Ignore summary heading
        if line.startswith("## 개정 요지") or line.startswith("## 개정요지"):
            flush_entry()
            cur_entry = None
            continue

        # Title → from/to version
        tm = TITLE_RE.match(line)
        if tm:
            from_version = int(tm.group(1))
            to_version = int(tm.group(2))
            continue
        if from_version is None or to_version is None:
            m = DIFF_HEADER_RE.match(line)
            if m:
                to_version = int(m.group(1))

        if effective is None:
            m = EFFECTIVE_RE.search(line)
            if m:
                effective = date.fromisoformat(m.group(1))

        entry_match = ENTRY_RE.match(line)
        if entry_match:
            flush_entry()
            kind = entry_match.group(3)
            change_type = {"개정": "modified", "신설": "added", "삭제": "removed"}[kind]
            cur_entry = {
                "article_number": entry_match.group(1),
                "change_type": change_type,
                "title": entry_match.group(2),
            }
            cur_sub = None
            continue

        sub_match = SUBSECTION_RE.match(line)
        if sub_match:
            flush_sub()
            cur_sub = sub_match.group(1)
            continue

        if cur_entry is not None and cur_sub is not None:
            buf.append(line)

    flush_entry()

    if from_version is None or to_version is None:
        # Infer from path (regulation_v2_diff.md → from=1, to=2)
        name = path.stem
        m = re.search(r"v(\d+)_diff", name)
        if m:
            to_version = int(m.group(1))
            from_version = to_version - 1
        else:
            raise ValueError(f"cannot determine version range from {path}")

    if effective is None:
        raise ValueError(f"effective date not found in {path}")

    return ParsedRegulationDiff(
        from_version=from_version,
        to_version=to_version,
        effective_date=effective,
        summary=None,
        entries=entries,
    )


def apply_diff_to_articles(
    base_articles: list,
    diff: ParsedRegulationDiff,
):
    """Produce a new list of ParsedArticle for the target version by applying
    the diff on top of the base articles. Used for regulation_version v2/v3
    upsert so that full-text of every article is available for every version
    (not just the changed ones)."""
    from copy import deepcopy

    from .models import ParsedArticle

    by_number = {a.article_number: deepcopy(a) for a in base_articles}

    for entry in diff.entries:
        if entry.change_type == "modified":
            art = by_number.get(entry.article_number)
            if art is None:
                # treated as add if base missing
                art = ParsedArticle(
                    article_number=entry.article_number,
                    article_seq=_seq_from_number(entry.article_number),
                    chapter_number=None,
                    chapter_title=None,
                    title="(개정)",
                    body=entry.new_body or "",
                )
                by_number[entry.article_number] = art
            else:
                art.body = entry.new_body or art.body
        elif entry.change_type == "added":
            by_number[entry.article_number] = ParsedArticle(
                article_number=entry.article_number,
                article_seq=_seq_from_number(entry.article_number),
                chapter_number=None,
                chapter_title=None,
                title="(신설)",
                body=entry.new_body or "",
            )
        elif entry.change_type == "removed":
            by_number.pop(entry.article_number, None)

    return sorted(by_number.values(), key=lambda a: a.article_seq)


def _seq_from_number(article_number: str) -> int:
    m = re.match(r"제(\d+)조(?:의(\d+))?", article_number)
    if not m:
        return 9999
    n = int(m.group(1))
    sub = int(m.group(2)) if m.group(2) else 0
    return n * 10 + sub
