"""Parse meeting markdown files (synthetic/meetings/*.md).

Layout:
    # 한빛마을 ... 회의록
    - **회의번호**: ...
    - **일시**: 2023년 11월 20일 ...
    - **유형**: 정기
    - **참석**: 19명 ...
    - **의결정족수**: 출석 과반수 10명 ...
    ...
    ## 안건 N. 제목
    ### 제안 배경
    ...
    ### 주요 토의
    ...
    ### 결정사항
    - **가결/부결/보류**. 결정문 ...
    - **투표**: 찬성 17 / 반대 0 / 기권 2
    - **관련 조문**: 제21조제1호, 제45조
    - **후속조치**: ...
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .models import ParsedDecision, ParsedMeeting

DATE_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
ATTEND_RE = re.compile(r"참석\*?\*?:\s*(\d+)")
QUORUM_RE = re.compile(r"의결정족수\*?\*?:\s*[^0-9]*(\d+)")
TYPE_RE = re.compile(r"유형\*?\*?:\s*(정기|임시)")

AGENDA_HEADER_RE = re.compile(r"^##\s+안건\s+(\d+)\.\s*(.+?)\s*$")
SUBSECTION_RE = re.compile(r"^###\s+결정사항")
ANY_H3_RE = re.compile(r"^###\s+")
RESULT_LINE_RE = re.compile(r"\*\*(가결|부결|보류)\*\*\.\s*(.*)$")
VOTE_RE = re.compile(
    r"찬성\s*(\d+)\s*/\s*반대\s*(\d+)\s*/\s*기권\s*(\d+)"
)
ARTICLES_RE = re.compile(r"제\d+조(?:제\d+호)?(?:의\d+)?")


def parse_meeting_markdown(path: Path) -> ParsedMeeting:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    meeting_date: date | None = None
    meeting_type: str | None = None
    attendees: int | None = None
    quorum: int | None = None

    decisions: list[ParsedDecision] = []
    cur_agenda_seq: int | None = None
    cur_topic: str | None = None
    in_decision_section = False
    decision_buf: list[str] = []

    def flush_decision() -> None:
        nonlocal cur_agenda_seq, cur_topic, in_decision_section, decision_buf
        if cur_agenda_seq is None or not decision_buf:
            decision_buf = []
            in_decision_section = False
            return
        block = "\n".join(decision_buf)

        result: str | None = None
        decision_text: str = ""
        vote_for = vote_against = vote_abstain = None
        related: list[str] = []
        follow_up: str | None = None

        for dl in decision_buf:
            dls = dl.strip().lstrip("-").strip()
            rm = RESULT_LINE_RE.search(dls)
            if rm and result is None:
                result = rm.group(1)
                decision_text = rm.group(2).strip()
                continue
            if dls.startswith("**투표**"):
                vm = VOTE_RE.search(dls)
                if vm:
                    vote_for = int(vm.group(1))
                    vote_against = int(vm.group(2))
                    vote_abstain = int(vm.group(3))
                continue
            if dls.startswith("**관련 조문**"):
                related = list(dict.fromkeys(ARTICLES_RE.findall(dls)))
                continue
            if dls.startswith("**후속조치**"):
                follow_up = dls.split("**", 2)[-1].lstrip(":").strip()
                continue

        if not decision_text:
            decision_text = block.strip()

        decisions.append(
            ParsedDecision(
                agenda_seq=cur_agenda_seq,
                topic=cur_topic or "",
                category=[],
                decision=decision_text,
                result=result,
                vote_for=vote_for,
                vote_against=vote_against,
                vote_abstain=vote_abstain,
                related_articles=related,
                follow_up=follow_up,
            )
        )
        decision_buf = []
        in_decision_section = False

    for raw in lines:
        line = raw.rstrip()

        if meeting_date is None:
            m = DATE_RE.search(line)
            if m and "일시" in line:
                meeting_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if meeting_type is None:
            m = TYPE_RE.search(line)
            if m:
                meeting_type = m.group(1)
        if attendees is None:
            m = ATTEND_RE.search(line)
            if m:
                attendees = int(m.group(1))
        if quorum is None:
            m = QUORUM_RE.search(line)
            if m:
                quorum = int(m.group(1))

        ah = AGENDA_HEADER_RE.match(line)
        if ah:
            flush_decision()
            cur_agenda_seq = int(ah.group(1))
            cur_topic = ah.group(2).strip()
            in_decision_section = False
            continue

        if SUBSECTION_RE.match(line):
            in_decision_section = True
            continue
        # Any other H3 ends the decision section
        if in_decision_section and ANY_H3_RE.match(line) and not SUBSECTION_RE.match(line):
            flush_decision()
            continue
        # New top-level section (##) ends everything
        if line.startswith("## ") and not line.startswith("## 안건"):
            flush_decision()
            cur_agenda_seq = None
            cur_topic = None
            continue

        if in_decision_section:
            decision_buf.append(line)

    flush_decision()

    if meeting_date is None or meeting_type is None:
        raise ValueError(f"meeting header (date/type) not found in {path}")

    return ParsedMeeting(
        meeting_date=meeting_date,
        meeting_type=meeting_type,
        attendees_count=attendees,
        quorum=quorum,
        raw_text=text,
        decisions=decisions,
    )
