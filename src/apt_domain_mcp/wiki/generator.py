"""LLM-driven wiki page generator.

For a given (complex_id, topic):
1. Gathers source rows from regulation_article (current version) and
   meeting_decision filtered by `topic = ANY(category)`.
2. Pulls revision history for the gathered articles.
3. Computes a stable source_hash so identical inputs skip re-generation.
4. Prompts the LLM to produce a markdown page.
5. Upserts the page into `wiki_page`.

The generator is idempotent: running it twice on unchanged data is a no-op
unless `force=True` is passed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import asyncpg

from ..ingest.llm_client import chat_text, get_model
from .repository import get_wiki_source_hash, upsert_wiki_page

# Default topic set for Phase 1 wiki. Expand when new evidence categories grow.
DEFAULT_TOPICS: tuple[str, ...] = (
    "주차",
    "관리비",
    "반려동물",
    "층간소음",
    "장기수선",
    "공사",
    "흡연",
    "공동시설",
    "회계",
)


@dataclass
class _ArticleSrc:
    article_number: str
    article_seq: int
    title: str
    body: str
    category: list[str]
    referenced_articles: list[str]
    referenced_laws: list[str]


@dataclass
class _RevisionSrc:
    article_number: str
    from_version: int
    to_version: int
    change_type: str
    reason: str | None
    effective_date: str


@dataclass
class _DecisionSrc:
    meeting_date: str
    meeting_type: str
    agenda_seq: int
    topic_text: str
    decision: str
    result: str | None
    related_articles: list[str]
    follow_up: str | None


@dataclass
class _TopicSources:
    articles: list[_ArticleSrc] = field(default_factory=list)
    revisions: list[_RevisionSrc] = field(default_factory=list)
    decisions: list[_DecisionSrc] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.articles and not self.decisions


async def _gather_sources(
    conn: asyncpg.Connection, *, complex_id: str, topic: str
) -> _TopicSources:
    src = _TopicSources()

    ver_row = await conn.fetchrow(
        "SELECT version FROM regulation_version WHERE complex_id = $1 AND is_current = true",
        complex_id,
    )
    if not ver_row:
        return src
    cur_version = ver_row["version"]

    art_rows = await conn.fetch(
        """
        SELECT article_number, article_seq, title, body, category,
               referenced_articles, referenced_laws
        FROM regulation_article
        WHERE complex_id = $1 AND version = $2 AND $3 = ANY(category)
        ORDER BY article_seq
        """,
        complex_id,
        cur_version,
        topic,
    )
    for r in art_rows:
        src.articles.append(
            _ArticleSrc(
                article_number=r["article_number"],
                article_seq=r["article_seq"],
                title=r["title"] or "",
                body=r["body"] or "",
                category=list(r["category"] or []),
                referenced_articles=list(r["referenced_articles"] or []),
                referenced_laws=list(r["referenced_laws"] or []),
            )
        )

    if src.articles:
        nums = [a.article_number for a in src.articles]
        rev_rows = await conn.fetch(
            """
            SELECT rr.article_number, rr.from_version, rr.to_version, rr.change_type,
                   rr.reason, rv.effective_date
            FROM regulation_revision rr
            JOIN regulation_version rv
                ON rv.complex_id = rr.complex_id AND rv.version = rr.to_version
            WHERE rr.complex_id = $1 AND rr.article_number = ANY($2::text[])
            ORDER BY rv.effective_date
            """,
            complex_id,
            nums,
        )
        for r in rev_rows:
            src.revisions.append(
                _RevisionSrc(
                    article_number=r["article_number"],
                    from_version=r["from_version"],
                    to_version=r["to_version"],
                    change_type=r["change_type"],
                    reason=r["reason"],
                    effective_date=r["effective_date"].isoformat(),
                )
            )

    dec_rows = await conn.fetch(
        """
        SELECT m.meeting_date, m.meeting_type, d.agenda_seq, d.topic, d.decision,
               d.result, d.related_articles, d.follow_up
        FROM meeting_decision d
        JOIN meeting m ON m.meeting_id = d.meeting_id
        WHERE d.complex_id = $1 AND $2 = ANY(d.category)
        ORDER BY m.meeting_date, d.agenda_seq
        """,
        complex_id,
        topic,
    )
    for r in dec_rows:
        src.decisions.append(
            _DecisionSrc(
                meeting_date=r["meeting_date"].isoformat(),
                meeting_type=r["meeting_type"],
                agenda_seq=r["agenda_seq"],
                topic_text=r["topic"] or "",
                decision=r["decision"] or "",
                result=r["result"],
                related_articles=list(r["related_articles"] or []),
                follow_up=r["follow_up"],
            )
        )

    return src


def _compute_source_hash(topic: str, src: _TopicSources) -> str:
    h = hashlib.sha256()
    h.update(topic.encode("utf-8"))
    for a in src.articles:
        h.update(f"\narticle::{a.article_number}::{a.body}".encode("utf-8"))
    for r in src.revisions:
        h.update(
            f"\nrevision::{r.article_number}::{r.from_version}->{r.to_version}::{r.reason or ''}".encode("utf-8")
        )
    for d in src.decisions:
        h.update(
            f"\ndecision::{d.meeting_date}::{d.agenda_seq}::{d.decision}".encode("utf-8")
        )
    return h.hexdigest()


def _serialize_user_prompt(topic: str, src: _TopicSources) -> str:
    parts: list[str] = [f"# 토픽: {topic}\n"]

    if src.articles:
        parts.append("## 관련 관리규약 조문 (현행 버전)\n")
        for a in src.articles:
            parts.append(f"### {a.article_number} {a.title}")
            if a.category:
                parts.append(f"- 카테고리: {', '.join(a.category)}")
            if a.referenced_articles:
                parts.append(f"- 본문 내 참조 조문: {', '.join(a.referenced_articles)}")
            if a.referenced_laws:
                parts.append(f"- 참조 법령: {', '.join(a.referenced_laws)}")
            parts.append("")
            parts.append("본문:")
            parts.append(a.body)
            parts.append("")
    else:
        parts.append("## 관련 관리규약 조문\n(해당 토픽 관련 조문 없음)\n")

    if src.revisions:
        parts.append("## 조문 개정 이력\n")
        for r in src.revisions:
            reason = r.reason or "(사유 미기재)"
            parts.append(
                f"- {r.effective_date} v{r.from_version}→v{r.to_version} "
                f"{r.article_number} ({r.change_type}): {reason}"
            )
        parts.append("")

    if src.decisions:
        parts.append("## 입주자대표회의 결정사항\n")
        for d in src.decisions:
            related = (
                f" / 관련조문 {', '.join(d.related_articles)}"
                if d.related_articles
                else ""
            )
            parts.append(f"### {d.meeting_date} {d.meeting_type} 안건 {d.agenda_seq}: {d.topic_text}")
            parts.append(f"- 결과: {d.result or '미기재'}{related}")
            parts.append(f"- 결정문: {d.decision}")
            if d.follow_up:
                parts.append(f"- 후속조치: {d.follow_up}")
            parts.append("")
    else:
        parts.append("## 입주자대표회의 결정사항\n(해당 토픽 관련 결정 없음)\n")

    return "\n".join(parts).strip()


_SYSTEM_PROMPT = """당신은 한국 공동주택 운영 위키 큐레이터입니다. 주어진 관리규약 조문과 입주자대표회의 결정사항을 종합해, 해당 단지의 **토픽별 운영 가이드 페이지**를 마크다운으로 작성합니다.

## 원칙
1. **원문 우선**: 관리규약 조문은 원문을 blockquote(`> `)로 그대로 인용합니다. 축약·의역·재서술 금지.
2. **사실 나열**: 결정사항은 날짜·안건·결과·후속조치를 객관적으로 요약합니다. 본인 의견이나 단정적 자문 금지.
3. **연대기 우선**: 결정 이력은 항상 날짜순으로, 이전 결정이 이후 결정의 배경이 되는 관계를 명시합니다.
4. **입주민 관점**: "누가, 언제, 어떤 기준으로, 얼마의 비용으로, 어떤 절차로" 라는 질문에 답할 수 있도록 구성합니다.
5. **법적 해석 금지**: "이것은 위법이다", "이것이 합법이다" 같은 판단은 하지 마세요. 상위 법령 언급은 사실만 인용합니다.

## 출력 형식
- 페이지 제목(h1)은 호출처가 따로 관리하므로 본문은 `## 개요` 부터 시작합니다.
- 구성은 다음 순서를 따릅니다. 해당 섹션의 자료가 없으면 그 섹션은 생략합니다.
  ```
  ## 개요
  (1~2문단: 이 토픽이 단지 관리체계에서 어떻게 다뤄지는지. 조문이 몇 개 있는지, 어떤 규정 범위인지.)

  ## 관리규약 조문
  ### 제XX조 (제목)
  > (원문 blockquote, 항·호 구조 유지)

  해설: (1~3문장, 본문의 의미를 풀어 설명. 원문 재작성 금지. 자의적 해석 금지.)

  ## 개정 연혁
  - YYYY-MM-DD vA→vB 제XX조 (변경유형): 사유
  ...

  ## 입주자대표회의 결정 이력
  ### YYYY-MM-DD (정기|임시) — 안건명
  - 결과: (가결|부결|보류)
  - 요약: (1~2문장 결정 내용)
  - 관련 조문: ...
  - 후속조치: ...
  ...

  ## 참고
  - 상위 법령 참조
  - 관련 토픽 (있으면)
  ```
- 섹션이 없으면(예: 조문 없음) 해당 섹션을 완전히 생략합니다. 빈 섹션 남기지 마세요.
- 전체 길이는 2000~6000자 권장. 패딩 금지, 실제 자료에 근거한 내용만.

## 응답
마크다운 본문만 반환합니다. 앞뒤 설명·코드펜스·JSON 금지."""


async def generate_topic_page(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    topic: str,
    force: bool = False,
) -> dict:
    """Generate or regenerate the wiki page for a single topic.
    Returns a status dict describing what happened.
    """
    src = await _gather_sources(conn, complex_id=complex_id, topic=topic)
    if src.is_empty():
        return {"topic": topic, "status": "skipped_no_evidence"}

    source_hash = _compute_source_hash(topic, src)

    if not force:
        existing = await get_wiki_source_hash(conn, complex_id=complex_id, topic=topic)
        if existing == source_hash:
            return {"topic": topic, "status": "skipped_unchanged", "hash": source_hash}

    user_prompt = _serialize_user_prompt(topic, src)
    body_md = chat_text(_SYSTEM_PROMPT, user_prompt, max_tokens=4096)
    if not body_md:
        return {"topic": topic, "status": "failed_llm"}

    # Strip any stray code fence
    if body_md.startswith("```"):
        body_md = body_md.strip("`")
        if body_md.startswith("markdown\n"):
            body_md = body_md[len("markdown\n"):]
        body_md = body_md.strip()

    source_refs: list[dict] = []
    for a in src.articles:
        source_refs.append({"type": "article", "id": a.article_number})
    for d in src.decisions:
        source_refs.append(
            {
                "type": "meeting_decision",
                "meeting_date": d.meeting_date,
                "agenda_seq": d.agenda_seq,
            }
        )

    title = f"{topic} 운영 가이드"

    await upsert_wiki_page(
        conn,
        complex_id=complex_id,
        topic=topic,
        title=title,
        body_md=body_md,
        source_refs=source_refs,
        source_hash=source_hash,
        generator_model=get_model(),
    )

    return {
        "topic": topic,
        "status": "generated",
        "article_count": len(src.articles),
        "decision_count": len(src.decisions),
        "body_chars": len(body_md),
        "hash": source_hash,
    }


async def generate_all_topics(
    conn: asyncpg.Connection,
    *,
    complex_id: str,
    topics: tuple[str, ...] = DEFAULT_TOPICS,
    force: bool = False,
) -> list[dict]:
    results: list[dict] = []
    for topic in topics:
        r = await generate_topic_page(
            conn, complex_id=complex_id, topic=topic, force=force
        )
        results.append(r)
    return results
