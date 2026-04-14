"""Parse the full regulation markdown (synthetic/regulation_v1.md style).

Structure expected:
    # Title
    ...meta block...
    ---
    ## 제N장 XXX
    ### 제N조 (제목)
    body lines...
    ### 제N+1조 (제목)
    ...

Front-matter meta lines ("- **버전**: v1", "- **시행일**: 2018-10-01") are
extracted when present. Any section not matching the patterns above is
accumulated into the *previous* article's body so항·호 번호(1. 2. 3.)도
그대로 붙는다.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .models import ParsedArticle, ParsedRegulation

CHAPTER_RE = re.compile(r"^##\s+제(\d+)장\s+(.+?)\s*$")
ARTICLE_RE = re.compile(r"^###\s+제(\d+)(?:조의(\d+))?조?\s*\((.+?)\)\s*$")
# more permissive fallback for "### 제N조 (title)"
ARTICLE_RE2 = re.compile(r"^###\s+제(\d+)조\s*\((.+?)\)\s*$")
VERSION_RE = re.compile(r"버전\*?\*?:\s*v?(\d+)", re.IGNORECASE)
EFFECTIVE_RE = re.compile(r"시행일\*?\*?:\s*(\d{4}-\d{2}-\d{2})")


def _normalize_article_number(n: int, sub: int | None) -> tuple[str, int]:
    if sub:
        return f"제{n}조의{sub}", n * 10 + sub
    return f"제{n}조", n * 10


def parse_regulation_markdown(path: Path, *, version: int | None = None) -> ParsedRegulation:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    meta_version = version
    meta_effective: date | None = None
    summary_bits: list[str] = []

    articles: list[ParsedArticle] = []
    cur_chapter_num: int | None = None
    cur_chapter_title: str | None = None
    cur_article: ParsedArticle | None = None
    body_buf: list[str] = []

    in_meta = True

    def flush_article() -> None:
        nonlocal cur_article, body_buf
        if cur_article is not None:
            cur_article.body = "\n".join(body_buf).strip()
            articles.append(cur_article)
        cur_article = None
        body_buf = []

    for raw in lines:
        line = raw.rstrip()

        if in_meta:
            if line.startswith("## 제") or line.startswith("### 제"):
                in_meta = False
            else:
                if meta_version is None:
                    m = VERSION_RE.search(line)
                    if m:
                        meta_version = int(m.group(1))
                if meta_effective is None:
                    m = EFFECTIVE_RE.search(line)
                    if m:
                        meta_effective = date.fromisoformat(m.group(1))
                if line.startswith("- **비고**"):
                    summary_bits.append(line[2:])
                if not in_meta:
                    pass  # falls through below
                else:
                    continue

        chapter_match = CHAPTER_RE.match(line)
        if chapter_match:
            flush_article()
            cur_chapter_num = int(chapter_match.group(1))
            cur_chapter_title = chapter_match.group(2).strip()
            continue

        article_match = ARTICLE_RE.match(line) or ARTICLE_RE2.match(line)
        if article_match:
            flush_article()
            groups = article_match.groups()
            n = int(groups[0])
            sub = int(groups[1]) if len(groups) == 3 and groups[1] else None
            title = groups[-1].strip()
            article_number, seq = _normalize_article_number(n, sub)
            cur_article = ParsedArticle(
                article_number=article_number,
                article_seq=seq,
                chapter_number=cur_chapter_num,
                chapter_title=cur_chapter_title,
                title=f"({title})",
                body="",
            )
            continue

        if cur_article is not None:
            # skip the horizontal rule "---"
            if line.strip() == "---":
                continue
            body_buf.append(line)

    flush_article()

    if meta_version is None:
        raise ValueError(f"regulation version not found in {path}")
    if meta_effective is None:
        raise ValueError(f"effective date not found in {path}")

    return ParsedRegulation(
        version=meta_version,
        effective_date=meta_effective,
        summary="; ".join(summary_bits) if summary_bits else None,
        articles=articles,
    )
