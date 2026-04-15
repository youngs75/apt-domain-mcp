"""Category / reference tagger.

Primary path: LiteLLM proxy (portal 내부 `us.anthropic.claude-sonnet-4-6`)로
구조화된 JSON 태깅. Fallback: 키워드 룰(이하 CATEGORY_RULES).

호출처(`seed.py`, `cli.py`)는 기존 `tag_article(article)` / `tag_decision(decision)`
시그니처만 사용하므로 본 모듈 내부 전환으로 완결된다.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from .llm_client import chat_json, get_client

log = logging.getLogger(__name__)

ALLOWED_CATEGORIES = [
    "총칙",
    "입주자",
    "대표회의",
    "관리주체",
    "관리비",
    "회계",
    "장기수선",
    "시설",
    "주차",
    "공동시설",
    "층간소음",
    "반려동물",
    "흡연",
    "공사",
    "선거",
    "분쟁",
    "보안",
    "기타",
]

# -- keyword fallback ---------------------------------------------------------

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


def categorize_keyword(text: str, extra_hint: str = "") -> list[str]:
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


# -- LLM primary path ---------------------------------------------------------

_ARTICLE_SYSTEM = (
    "당신은 한국 공동주택 관리규약 조문을 분류하는 태깅 어시스턴트입니다. "
    "입력된 조문 본문을 읽고 다음 고정 카테고리 목록 중에서 해당 조문이 "
    "실제로 규정하는 주제만 골라 반환하세요. 본문에 단순히 언급되는 단어에 "
    "끌려 과도하게 태깅하지 마세요.\n\n"
    f"허용 카테고리: {', '.join(ALLOWED_CATEGORIES)}\n\n"
    "추가로 본문에서 참조하는 다른 조문 번호(예: '제39조', '제21조제1호')와 "
    "상위 법령명(예: '공동주택관리법 제20조의2')도 추출하세요. "
    "자기 자신을 가리키는 조문 번호는 제외합니다.\n\n"
    "반드시 다음 JSON 스키마로만 응답하세요. 다른 텍스트 금지.\n"
    "{\n"
    '  "categories": [string, ...],   // 허용 카테고리에서만 선택, 최소 1개 최대 5개\n'
    '  "tags": [string, ...],         // 자유 태그(키워드 2~6개), 카테고리보다 세부\n'
    '  "referenced_articles": [string, ...],  // "제N조" 또는 "제N조제N호"\n'
    '  "referenced_laws": [string, ...]       // 상위 법령명 전체\n'
    "}"
)

_DECISION_SYSTEM = (
    "당신은 한국 공동주택 입주자대표회의 회의록의 안건·결정사항을 분류하는 "
    "태깅 어시스턴트입니다. 주어진 안건 제목과 결정 내용을 읽고, 다음 고정 "
    "카테고리 목록 중 해당 결정의 주제만 골라 반환하세요.\n\n"
    f"허용 카테고리: {', '.join(ALLOWED_CATEGORIES)}\n\n"
    "반드시 다음 JSON 스키마로만 응답하세요. 다른 텍스트 금지.\n"
    "{\n"
    '  "categories": [string, ...],   // 허용 카테고리에서만, 최소 1개 최대 5개\n'
    '  "tags": [string, ...]          // 자유 태그(키워드 2~6개)\n'
    "}"
)


def _filter_allowed(cats: list) -> list[str]:
    if not isinstance(cats, list):
        return []
    return [c for c in cats if isinstance(c, str) and c in ALLOWED_CATEGORIES]


def _clean_str_list(xs, limit: int = 10) -> list[str]:
    if not isinstance(xs, list):
        return []
    out: list[str] = []
    for x in xs:
        if isinstance(x, str) and x.strip():
            s = x.strip()
            if s not in out:
                out.append(s)
        if len(out) >= limit:
            break
    return out


@lru_cache(maxsize=256)
def _llm_tag_article_cached(article_number: str, title: str, body: str) -> tuple | None:
    user = (
        f"조문 번호: {article_number}\n"
        f"제목: {title}\n"
        f"본문:\n{body}"
    )
    result = chat_json(_ARTICLE_SYSTEM, user, max_tokens=600)
    if not result:
        return None
    cats = _filter_allowed(result.get("categories", []))
    tags = _clean_str_list(result.get("tags", []), limit=8)
    refs = _clean_str_list(result.get("referenced_articles", []), limit=15)
    laws = _clean_str_list(result.get("referenced_laws", []), limit=10)
    if not cats:
        return None
    return (tuple(cats), tuple(tags), tuple(refs), tuple(laws))


@lru_cache(maxsize=256)
def _llm_tag_decision_cached(topic: str, decision_text: str) -> tuple | None:
    user = f"안건 제목: {topic}\n결정 내용:\n{decision_text}"
    result = chat_json(_DECISION_SYSTEM, user, max_tokens=300)
    if not result:
        return None
    cats = _filter_allowed(result.get("categories", []))
    tags = _clean_str_list(result.get("tags", []), limit=8)
    if not cats:
        return None
    return (tuple(cats), tuple(tags))


# -- public API (unchanged signatures) ---------------------------------------

def tag_article(article) -> None:
    """Mutate a ParsedArticle with derived metadata in-place."""
    # reference extraction is always regex-based (cheap, deterministic)
    article.referenced_articles = extract_referenced_articles(
        article.body, self_number=article.article_number
    )
    article.referenced_laws = extract_referenced_laws(article.body)

    llm_result = None
    if get_client() is not None:
        llm_result = _llm_tag_article_cached(
            article.article_number,
            article.title or "",
            article.body,
        )

    if llm_result is not None:
        cats, tags, refs, laws = llm_result
        article.category = list(cats)
        article.tags = list(tags)
        # LLM 결과가 더 풍부하면 병합 (regex에서 놓친 참조 흡수)
        for r in refs:
            if r not in article.referenced_articles and r != article.article_number:
                article.referenced_articles.append(r)
        for l in laws:
            if l not in article.referenced_laws:
                article.referenced_laws.append(l)
        return

    # fallback: keyword rules
    hint = f"{article.title} {article.chapter_title or ''}"
    article.category = categorize_keyword(article.body, extra_hint=hint)
    article.tags = []


def tag_decision(decision) -> None:
    llm_result = None
    if get_client() is not None:
        llm_result = _llm_tag_decision_cached(
            decision.topic or "",
            decision.decision or "",
        )

    if llm_result is not None:
        cats, tags = llm_result
        decision.category = list(cats)
        # ParsedDecision 현재 스키마에 tags 필드는 없음 — 확장 시 주석 해제
        # decision.tags = list(tags)
        return

    decision.category = categorize_keyword(decision.decision, extra_hint=decision.topic)
