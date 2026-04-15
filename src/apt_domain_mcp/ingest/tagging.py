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

# 각 카테고리는 본문에 반드시 포함되어야 하는 trigger 키워드를 가진다.
# LLM 응답 검증 시, 본문에 trigger 중 하나도 없으면 해당 카테고리를 자동 제거한다.
# false positive 차단용 안전망이며, trigger는 회의록 결정 검증에도 동일 적용한다.
CATEGORY_TRIGGERS: dict[str, tuple[str, ...]] = {
    "총칙": ("목적", "적용범위", "용어의 정의", "기본이념"),
    "입주자": ("입주자의 권리", "입주자의 의무", "입주자등의 의무"),
    "대표회의": ("입주자대표회의", "동별 대표자", "회장", "부회장", "이사회", "임원의 임기"),
    "관리주체": ("관리주체", "관리사무소장", "위탁관리", "관리직원"),
    "관리비": ("관리비", "사용료", "부과기준", "예비비", "연체료"),
    "회계": ("회계연도", "예산", "결산", "감사", "외부회계감사", "잡수입", "회계감사"),
    "장기수선": ("장기수선", "장충금", "수선유지비", "장기수선충당금"),
    "시설": ("시설관리", "부대시설", "승강기", "옥상", "방수", "조명", "LED", "전기차 충전", "수목"),
    "주차": ("주차장", "방문차량", "주차"),
    "공동시설": ("주민공동시설", "어린이집", "놀이터", "커뮤니티", "택배"),
    "층간소음": ("층간소음",),
    "반려동물": ("반려동물", "애완동물", "반려견", "반려묘"),
    "흡연": ("흡연", "금연", "담배"),
    "공사": ("공사", "입찰", "수의계약", "제한경쟁입찰", "공고"),
    "선거": ("선거관리위원회", "선거", "선출", "동별 대표자 선출"),
    "분쟁": ("분쟁", "조정", "중재", "층간소음", "분쟁조정"),
    "보안": ("CCTV", "경비", "보안"),
    "기타": (),  # 언제나 허용
}


_ARTICLE_SYSTEM = """당신은 한국 공동주택 관리규약 조문을 분류하는 태깅 어시스턴트입니다.

## 규칙
1. 아래 허용 카테고리 중에서, 해당 조문이 **직접적으로 규정하는 주제**만 선택합니다.
2. 본문에 단어가 단순히 등장한다는 이유로 카테고리를 붙이지 마세요.
   예: 본문에 "입주자대표회의의 의결로 정한다"라는 행정 문구가 있다고 해서 '대표회의' 카테고리를 붙이면 안 됩니다.
       '대표회의' 카테고리는 대표회의의 구성·소집·의결 자체를 규정하는 조문에만 붙입니다.
3. **카테고리는 최소 1개, 최대 3개**. 과다 태깅은 검색 품질을 해칩니다. 확신 없는 카테고리는 제외하세요.
4. 본문에 해당 카테고리의 핵심 용어가 명시적으로 등장해야 합니다. (예: '반려동물' 카테고리는 본문에 '반려동물/애완동물/반려견/반려묘'라는 단어가 있어야만 선택 가능)
5. 참조 조문(예: '제39조', '제21조제1호')과 상위 법령(예: '공동주택관리법')도 본문에서 명시적으로 언급된 것만 추출합니다. 자기 자신은 제외.

## 허용 카테고리
총칙, 입주자, 대표회의, 관리주체, 관리비, 회계, 장기수선, 시설, 주차, 공동시설, 층간소음, 반려동물, 흡연, 공사, 선거, 분쟁, 보안, 기타

## Few-shot 예시

예시 1 (직접 규정):
조문: 제85조 (반려동물의 사육)
본문: "① 입주자등은 반려동물을 사육할 때 다음 각 호를 준수하여야 한다. 1. 공용부 이동 시 목줄을 착용한다. ..."
정답: {"categories":["반려동물","공동시설"], "tags":["반려동물 사육","목줄","공용부"], "referenced_articles":[], "referenced_laws":[]}

예시 2 (부수 언급 주의):
조문: 제13조 (입주자대표회의의 구성)
본문: "① 대표회의는 200세대 이상 동에서 선출된 동별 대표자로 구성하며 정원은 21명으로 한다. ② 동별 대표자는 입주자등의 직접 선거로 선출한다. ..."
정답: {"categories":["대표회의","선거"], "tags":["대표회의 구성","동별 대표자","정원"], "referenced_articles":[], "referenced_laws":[]}
설명: 본문에 '입주자등'이 등장하지만 '입주자' 카테고리(권리·의무)는 해당 없음. '선거'는 선출 규정이 실제로 있어 포함.

예시 3 (행정 문구 함정):
조문: 제41조 (관리비 부과기준)
본문: "① 관리비는 공용관리비와 개별사용료로 구분하여 부과한다. ② 공용전기료는 세대별 전용면적에 비례하여 부과한다. ③ 세부 사항은 입주자대표회의의 의결로 정한다."
정답: {"categories":["관리비"], "tags":["부과기준","공용전기료","전용면적"], "referenced_articles":[], "referenced_laws":[]}
설명: '입주자대표회의의 의결' 문구는 흔한 행정 상투어이므로 '대표회의' 카테고리를 붙이면 안 됨. '관리비' 단일 카테고리.

## 응답 형식
반드시 다음 JSON 스키마로만 응답. 다른 텍스트·마크다운 금지.
{
  "categories": [string, ...],
  "tags": [string, ...],
  "referenced_articles": [string, ...],
  "referenced_laws": [string, ...]
}"""


_DECISION_SYSTEM = """당신은 한국 공동주택 입주자대표회의 회의록의 안건·결정사항을 분류하는 태깅 어시스턴트입니다.

## 규칙
1. 아래 허용 카테고리 중에서, 해당 안건이 **직접적으로 다루는 주제**만 선택합니다.
2. **카테고리는 최소 1개, 최대 3개**. 과다 태깅 금지.
3. 안건 제목과 결정문에 해당 카테고리의 핵심 용어가 명시적으로 등장해야 합니다.
4. 안건의 "핵심이 무엇이냐"로 판단하세요. 재원이 장충금이라고 해서 모든 안건에 '장기수선' 카테고리를 붙이면 안 됩니다. 장기수선계획의 수립·변경·집행 근거 자체를 논의하는 안건에만 '장기수선'을 붙입니다.

## 허용 카테고리
총칙, 입주자, 대표회의, 관리주체, 관리비, 회계, 장기수선, 시설, 주차, 공동시설, 층간소음, 반려동물, 흡연, 공사, 선거, 분쟁, 보안, 기타

## Few-shot 예시

예시 1:
안건: 공용부 조명 LED 교체 입찰 공고안 승인의 건
결정: 가결. LED 교체 공사 입찰을 일반경쟁입찰 종합평가 방식으로 공고한다. 예정가격 6,800만원 한도, 집행 계정은 장기수선충당금.
정답: {"categories":["공사","시설"], "tags":["LED","입찰","공용조명"]}
설명: 장충금으로 집행하지만 안건의 핵심은 공사 입찰. '장기수선'은 붙이지 않음.

예시 2:
안건: 104동 층간소음 갈등 3단계 분쟁조정 회부 건의의 건
결정: 가결. 104동 12XX호 ↔ 11XX호 층간소음 갈등 건을 중앙공동주택관리분쟁조정위원회에 공식 회부한다.
정답: {"categories":["층간소음","분쟁"], "tags":["3단계 회부","분쟁조정위원회"]}

예시 3:
안건: 2024년도 예산안 승인의 건
결정: 가결. 2024년도 관리비 예산안을 총액 18억 2천만원으로 승인한다.
정답: {"categories":["관리비","회계"], "tags":["예산안","연간예산"]}

## 응답 형식
반드시 다음 JSON 스키마로만 응답. 다른 텍스트·마크다운 금지.
{
  "categories": [string, ...],
  "tags": [string, ...]
}"""


def _apply_trigger_gate(cats: list[str], haystack: str) -> list[str]:
    """LLM이 반환한 카테고리 중, 본문에 trigger 키워드가 하나도 없는 것을 제거.
    false positive 차단용 안전망."""
    out: list[str] = []
    for c in cats:
        triggers = CATEGORY_TRIGGERS.get(c)
        if triggers is None:
            # unknown 카테고리는 이미 _filter_allowed에서 걸러짐
            continue
        if not triggers:  # "기타"처럼 trigger 없는 카테고리는 그대로 허용
            out.append(c)
            continue
        if any(t in haystack for t in triggers):
            out.append(c)
    return out


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
    cats_raw = _filter_allowed(result.get("categories", []))
    haystack = f"{title}\n{body}"
    cats = _apply_trigger_gate(cats_raw, haystack)
    # LLM이 정확히 3개를 반환하면 그대로, 초과 시 상위 3개만 유지
    cats = cats[:3]
    tags = _clean_str_list(result.get("tags", []), limit=8)
    refs = _clean_str_list(result.get("referenced_articles", []), limit=15)
    laws = _clean_str_list(result.get("referenced_laws", []), limit=10)
    if not cats:
        cats = ["기타"]
    return (tuple(cats), tuple(tags), tuple(refs), tuple(laws))


@lru_cache(maxsize=256)
def _llm_tag_decision_cached(topic: str, decision_text: str) -> tuple | None:
    user = f"안건 제목: {topic}\n결정 내용:\n{decision_text}"
    result = chat_json(_DECISION_SYSTEM, user, max_tokens=300)
    if not result:
        return None
    cats_raw = _filter_allowed(result.get("categories", []))
    haystack = f"{topic}\n{decision_text}"
    cats = _apply_trigger_gate(cats_raw, haystack)[:3]
    tags = _clean_str_list(result.get("tags", []), limit=8)
    if not cats:
        cats = ["기타"]
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
