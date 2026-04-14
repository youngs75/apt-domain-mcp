# 01. Architecture

## 1. 설계 배경

공동주택 도메인 지식은 세 가지 특성이 섞여 있다:

1. **구조가 강한 문서**(관리규약) — 조/항/호 계층 명시적, 카테고리 유한, 질의 패턴 좁음
2. **반구조화 문서**(회의록) — 날짜·안건·결정이라는 스캐폴드는 있으나 본문은 자유서술
3. **구조화 수치**(관리비·감사·장기수선) — 시계열 테이블

"RAG + Vector DB" 일률 적용은 1·3에 대해 비효율적이다. 구조가 이미 있는 문서를 벡터 공간에 납작하게 눌러넣으면 검색 품질이 오히려 떨어진다. 본 서버는 **메타데이터 우선주의**로 3계층 저장소를 구성한다.

## 2. 3계층 저장소 모델

```
┌─────────────────────────────────────────────────────────────┐
│ L1. PostgreSQL (코어·single source of truth)                │
│   - complex, regulation_article, regulation_revision        │
│   - meeting, meeting_decision                               │
│   - document (원본 PDF/HWP 메타)                            │
│   - tsvector FTS 인덱스 (pg_bigm 또는 mecab-ko)             │
├─────────────────────────────────────────────────────────────┤
│ L2. LLM Wiki (파생, 재생성 가능)                            │
│   - wiki_page 테이블 (topic, complex_id, body_md, sources)  │
│   - 소스 해시 변동 시 재생성                                │
│   - MCP resource://complex/{id}/wiki/{topic} 로 노출        │
├─────────────────────────────────────────────────────────────┤
│ L3. Milvus (Phase 1 후반, AWS EKS 상 기존 인프라 활용)      │
│   - 회의록 chunk 임베딩만. 관리규약 제외.                   │
│   - complex_id partition/메타데이터 필수(테넌트 격리)       │
└─────────────────────────────────────────────────────────────┘
```

### L1: PostgreSQL가 단일 진실원

- 관리규약은 **조문 단위 row**로 정규화. 개정은 `version` 컬럼으로 append-only, 이전 버전은 `is_current=false`.
- FTS는 한국어 형태소 분석기(`mecab-ko` 또는 n-gram 기반 `pg_bigm`) 위에 `tsvector` 인덱스.
- 카테고리·태그는 인제스트 타임에 **LLM으로 사전 태깅**하여 `TEXT[]` 컬럼에 저장. 조회 타임엔 LLM 호출 0회로 필터 가능.

### L2: LLM Wiki는 사용자 친화 계층

- 입주민이 실제로 원하는 것: "관리규약 제X조" 원문보다 "주차 관련 규정 종합"
- 토픽(예: "주차", "관리비", "반려동물", "층간소음", "선거")별로 LLM이 소스를 모아 **큐레이션된 마크다운 페이지**를 생성
- PostgreSQL `wiki_page` row에 저장: `(complex_id, topic, body_md, source_hashes, last_generated_at)`
- 소스 문서(관리규약/회의록)의 해시가 바뀌면 영향받는 위키 페이지를 재생성하는 워커 필요(Phase 1 후반)
- MCP에서는 `resource://complex/{complex_id}/wiki/{topic}` URI로 노출

### L3: Vector는 회의록에만, Phase 1부터 선택적 도입 가능

- 관리규약은 구조+FTS로 충분. 벡터 필요 없음. **이 판단은 인프라 여부와 무관하게 유지**.
- 회의록은 자유서술 + 과거 결정 간 유사도 검색 니즈 → 벡터 유의미
- **배포 환경(AWS EKS)에 PostgreSQL과 Milvus가 이미 가용**하므로 "인프라 프로비저닝 비용 때문에 미루는" 이유는 없다. Phase 1에서 다음 순서로 진행한다:
  1. PostgreSQL FTS만으로 회의록 질의를 먼저 붙이고
  2. 실제 질의 결과에서 "키워드 불일치로 놓친 케이스"가 측정되면 즉시 Milvus 인덱스 추가
  3. 관리규약은 벡터 인덱스 후보에서 영구 제외 (3계층 모델의 핵심 판단)
- Milvus collection 스키마 잠정: `meeting_chunks` with metadata `{complex_id, meeting_id, meeting_date, decision_id?, category[]}`. `complex_id`는 partition key 또는 필수 필터로 테넌트 격리.

## 3. 멀티테넌시

- **단지 식별자**: 내부 ULID `complex_id` 1급 키. 외부 식별자(K-apt `kaptCode`, 지자체 `apt_seq`)는 `complex.external_ids JSONB`에 부록으로 저장.
- **모든 테이블**에 `complex_id NOT NULL` + FK.
- **모든 tool의 첫 파라미터**는 `complex_id`. tool handler 레벨에서 누락 시 즉시 `INVALID_PARAMS`.
- **row-level security는 Phase 2**. 현재는 애플리케이션 레벨 강제.

## 4. 인제스트 파이프라인

```
원본 문서 (PDF/HWP)
   ↓ 1. 파싱 (pypdf / pdfplumber / pyhwp)
텍스트 + 구조 힌트(페이지, 표)
   ↓ 2. 분류 (규약 / 회의록 / 감사 / 공고)
   ↓ 3a. 규약: 조/항/호 정규식 + LLM 보조 분할
   ↓ 3b. 회의록: 안건·결정 LLM 추출
   ↓ 4. 메타 태깅 (LLM이 category, tags, referenced_articles 생성)
   ↓ 5. PostgreSQL upsert (규약은 version 증가)
   ↓ 6. 영향받는 wiki_page invalidate → 재생성 큐
```

CLI 엔트리포인트: `python -m apt_domain_mcp.ingest.cli --complex-id <id> --file <path> --kind regulation|meeting|audit|notice`

## 5. MCP Tool 명세 (Phase 1 목표)

### search_regulation
| 파라미터 | 타입 | 설명 |
|---|---|---|
| `complex_id` | str | **필수** |
| `query` | str | 키워드 (조번호 또는 본문) |
| `category` | str? | 선택 필터 (예: "관리비") |
| `as_of_date` | date? | 특정 시점 기준 유효 조문 |
| `display` | int=20 | |

응답: 조문 리스트(조번호, 제목, 본문, version, category, tags)

### get_regulation_article
| `complex_id`, `article_number`, `version`(선택, default=current) |

### list_regulation_revisions
| `complex_id`, `article_number`(선택) | → 개정 이력 타임라인

### search_meeting_decisions
| `complex_id`, `query`, `date_from`, `date_to`, `category` | → 결정사항 리스트 (회의일, 안건, 결정, 관련 규약 조문)

### get_meeting_detail
| `complex_id`, `meeting_id` | → 참석자, 전체 안건, 결정, 원본 문서 링크

### get_wiki_page
| `complex_id`, `topic` | → 마크다운 body + 소스 references + last_generated_at

### list_complexes (운영용)
| 없음 | → 서버가 서빙 중인 단지 목록. 인증 필요(Phase 2).

## 6. 주요 결정 및 미해결 사항

### 결정됨
- 관리규약은 조문 단위 정규화, append-only 버전
- 위키는 PostgreSQL row (파일 시스템 아님)
- 배포 환경(AWS EKS)에 PostgreSQL·Milvus 모두 가용 → 벡터 도입은 **인프라 이슈가 아닌 코퍼스 적합성 이슈**로 판단
- Phase 1: 회의록 FTS 먼저 → 실측 후 Milvus 추가. 관리규약은 영구적으로 벡터 인덱스 대상 아님.
- `complex_id`는 내부 ULID, 외부 ID는 부록

### 미해결 (다음 세션)
- FTS analyzer 선택: pg_bigm vs mecab-ko (pg_bigm이 설치 간편, mecab-ko가 품질 우위)
- LLM provider: 위키 생성용 — Claude API vs 포털 내 제공 LLM
- 위키 재생성 트리거: 동기(인제스트 직후) vs 비동기(큐)
- 문서 접근 권한 모델(입주민/관리소/외부): 현재는 미구현, Phase 2

## 7. kor-legal-mcp와의 경계

| 질의 | 담당 |
|---|---|
| "공동주택관리법 제93조 뭐야" | kor-legal-mcp (`get_law_article`) |
| "부천시 공동주택 관리규약 준칙 찾아줘" | kor-legal-mcp (`search_admrule` 또는 `search_ordinance`) |
| "우리 단지 관리규약 제38조" | **apt-domain-mcp** (`get_regulation_article`) |
| "지난 3월 입대의에서 주차 관련 뭐 결정했어" | **apt-domain-mcp** (`search_meeting_decisions`) |
| "우리 단지 주차 규정 종합 요약해줘" | **apt-domain-mcp** (`get_wiki_page topic=주차`) |

상위 Vertical Agent(`apt-legal-agent`)는 두 MCP 서버를 모두 연결하고, 사용자 질의를 분해해 적절히 라우팅한다.
