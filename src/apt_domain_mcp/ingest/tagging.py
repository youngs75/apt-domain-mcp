"""Keyword-based category tagger (LLM stub).

Phase 1 minimum viable tagger: maps body text to a set of categories from
docs/02_synthetic_complex_spec.md §3. Real LLM-assisted tagging (richer
categories, accurate tag disambiguation, referenced_articles extraction with
context) is deferred to Phase 1 후반.
"""
from __future__ import annotations

import re

# category → list of keyword patterns (substring match, case-insensitive on 한글 no-op)
CATEGORY_RULES: dict[str, list[str]] = {
    "총칙": ["목적", "적용범위", "용어의 정의", "기본이념"],
    "입주자": ["입주자등", "입주자의 권리", "입주자의 의무"],
    "대표회의": [
        "입주자대표회의",
        "동별 대표자",
        "회장",
        "부회장",
        "이사",
        "임원의 임기",
        "대표회의의 소집",
        "의결정족수",
    ],
    "관리주체": ["관리주체", "관리사무소장", "위탁관리", "관리직원", "관리규정의 비치"],
    "관리비": ["관리비", "사용료", "부과기준", "납부", "연체료", "예비비", "관리비의 공개"],
    "회계": ["회계연도", "예산", "결산", "감사", "외부회계감사"],
    "장기수선": ["장기수선", "장기수선충당금", "장충금", "수선유지비"],
    "시설": ["시설관리", "부대시설", "승강기", "옥상", "방수", "조명", "LED"],
    "주차": ["주차장", "방문차량", "주차"],
    "공동시설": ["주민공동시설", "어린이집", "놀이터", "커뮤니티"],
    "층간소음": ["층간소음"],
    "반려동물": ["반려동물", "애완동물", "개", "고양이"],
    "흡연": ["흡연", "금연"],
    "공사": ["공사", "입찰", "공고", "수의계약", "제한경쟁입찰"],
    "선거": ["선거관리위원회", "선거", "선출"],
    "분쟁": ["분쟁", "조정", "중재"],
    "보안": ["CCTV", "경비", "보안"],
}

ARTICLE_REF_RE = re.compile(r"제(\d+)조(?:의(\d+))?(?:제(\d+)항)?(?:제(\d+)호)?")
LAW_REF_RE = re.compile(r"(공동주택관리법|주택법|민법)(?:\s*시행령)?(?:\s*시행규칙)?(?:\s*제\d+조(?:의\d+)?)?")


def categorize(text: str, extra_hint: str = "") -> list[str]:
    haystack = f"{extra_hint}\n{text}"
    out: list[str] = []
    for cat, kws in CATEGORY_RULES.items():
        for kw in kws:
            if kw in haystack:
                out.append(cat)
                break
    return out


def extract_referenced_articles(body: str, self_number: str | None = None) -> list[str]:
    found: list[str] = []
    for m in ARTICLE_REF_RE.finditer(body):
        n = int(m.group(1))
        sub = m.group(2)
        article = f"제{n}조" + (f"의{sub}" if sub else "")
        if article == self_number:
            continue
        if article not in found:
            found.append(article)
    return found


def extract_referenced_laws(body: str) -> list[str]:
    found: list[str] = []
    for m in LAW_REF_RE.finditer(body):
        s = m.group(0).strip()
        if s not in found:
            found.append(s)
    return found


def tag_article(article) -> None:
    """Mutate a ParsedArticle with derived metadata in-place."""
    hint = f"{article.title} {article.chapter_title or ''}"
    article.category = categorize(article.body, extra_hint=hint)
    article.referenced_articles = extract_referenced_articles(
        article.body, self_number=article.article_number
    )
    article.referenced_laws = extract_referenced_laws(article.body)
    article.tags = []  # reserved for LLM tagging later


def tag_decision(decision) -> None:
    decision.category = categorize(decision.decision, extra_hint=decision.topic)
