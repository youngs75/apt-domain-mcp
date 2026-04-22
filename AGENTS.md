# Repository Guidelines

## 프로젝트 개요
`apt-domain-mcp` — 공동주택 **단지별 도메인 지식**을 제공하는 MCP 서버.
관리규약(조문 단위), 입주자대표회의 회의록, 공고·감사·장기수선계획 등 비공개·준공개 운영 산출물을 단지(`complex_id`) 단위로 색인한다.
법령·판례·자치법규 일반 조회는 sibling 리포 `kor-legal-mcp`의 책임이며, 본 서버는 해당 데이터를 직접 쿼리하지 않는다.

## 파이프라인 내 위치
```
사용자 질의
     ↓
apt-legal-agent  (Vertical AI Agent, minyoung-mah 기반)
       ↓ MCP
   ┌───┴────────────────────────┐
   ↓                            ↓
kor-legal-mcp              apt-domain-mcp  ← 본 리포
```

- `apt-legal-agent`는 사용자 질의를 분해·라우팅·종합하는 상위 Vertical Agent. 멀티에이전트 오케스트레이션은 자체 개발 라이브러리 `minyoung-mah`(사용자가 AX Advanced 미니 프로젝트 `ax-coding-agent`에서 추출한 harness)를 사용.
- 본 리포는 `complex_id` 기반 단지 도메인 지식 공급만 담당. 법령·판례 조회는 `kor-legal-mcp`의 책임이며 본 서버는 해당 데이터를 직접 쿼리하지 않는다.

## 문서 허브

3개 리포(`kor-legal-mcp`, `apt-domain-mcp`, `apt-legal-agent`)의 **cross-cutting 문서**(전체 아키텍처, 합성 단지 스펙, 로드맵 등)는 `apt-legal-agent` 리포의 `docs/`에서 관리한다. 본 리포는 구현·운영 문서(본 `AGENTS.md`, `sql/schema.sql` 등)만 유지.

- 전체 아키텍처: [apt-legal-agent/docs/01_architecture.md](https://github.com/youngs75/apt-legal-agent/blob/main/docs/01_architecture.md)
- 파일럿 단지 스펙 (한빛마을 새솔아파트): [apt-legal-agent/docs/02_synthetic_complex_spec.md](https://github.com/youngs75/apt-legal-agent/blob/main/docs/02_synthetic_complex_spec.md)
- Phase별 로드맵: [apt-legal-agent/docs/03_roadmap.md](https://github.com/youngs75/apt-legal-agent/blob/main/docs/03_roadmap.md)

## 책임 경계
- 본 서버는 단지 도메인 데이터(complex, regulation_*, meeting_*, document, wiki_page)의 **유일한 소유자**.
- 외부 리포(apt-web 등)는 **MCP tool 경로** 또는 **admin REST API 경로**(`/admin/api/*`)로만 접근. asyncpg 직접 접근 금지.

## Admin REST API 개요

인증: `X-Admin-API-Key` 헤더 + env `ADMIN_API_KEY` (빈 문자열이면 전면 거부).

| Method | Path | 설명 |
|---|---|---|
| GET | `/admin/api/complexes` | 단지 목록 |
| POST | `/admin/api/complexes` | 단지 생성/upsert. 필수: `name`, `address`. `complex_id` 선택 — 생략 시 서버가 ULID 자동 발급. 응답: `{"complex_id":"01HZZ...","generated":true,"status":"created"}` |
| DELETE | `/admin/api/complexes/{id}` | 단지 하드 삭제 (confirm_name 필수) |
| GET | `/admin/api/complexes/{id}/regulations` | 관리규약 버전 리스트 |
| GET | `/admin/api/complexes/{id}/meetings` | 회의록 리스트 |
| GET | `/admin/api/complexes/{id}/documents` | 문서 리스트 |
| POST | `/admin/api/complexes/{id}/ingest` | 파일 업로드 + 인제스트 |

### DELETE `/admin/api/complexes/{id}`

단지를 **하드 삭제**한다. `regulation_version`, `regulation_article`, `regulation_revision`, `meeting`, `decision`, `document`, `wiki_page` 등 종속 데이터가 함께 제거된다.

- Query: `confirm_name=<단지 이름 URL-encoded>` (필수, 안전장치)
- 200 `{"complex_id": "...", "status": "deleted"}`
- 400 `INVALID_PARAMS` — confirm_name 누락
- 404 `COMPLEX_NOT_FOUND`
- 409 `NAME_MISMATCH` — DB의 name 과 confirm_name 불일치 (strict 비교, trim 없음)
- 503 `DB_NOT_CONFIGURED`
- 500 `INTERNAL_ERROR`

구현은 단일 트랜잭션 안에서 `complex` 를 삭제한다. 모든 종속 테이블은 FK `ON DELETE CASCADE` 로 자동 정리된다 (`regulation_revision` FK 포함 — `migrate_regulation_revision_fk_cascade.sql` 적용 완료).

## 설계 원칙
- **멀티테넌트 단일 서버**: 단지당 서버를 띄우지 않고, 한 서버 인스턴스가 여러 단지를 서빙한다. `complex_id`(내부 ULID 또는 K-apt `kaptCode`)는 모든 tool 호출과 모든 테이블의 1급 키다.
- **저장소 3계층**:
  1. **PostgreSQL (코어)** — 관리규약 조문 단위 구조화, 개정 이력, 회의록 메타/안건, 위키 페이지. Full-text search는 `tsvector` + 한국어 analyzer(pg_bigm 또는 mecab-ko).
  2. **LLM Wiki** — 토픽별 큐레이션 페이지. PostgreSQL 테이블 row + MCP `resource://`로 노출. 소스 문서 해시 변동 시 자동 재생성 트리거.
  3. **Milvus (Vector)** — 회의록 자유서술부에만 적용. 관리규약에는 적용하지 않음(구조가 이미 있으므로 낭비). 배포 타깃인 AWS EKS에 PostgreSQL·Milvus 모두 기존 제공되므로 Phase 1 후반에 실 질의 데이터로 필요성 검증 후 즉시 도입 가능.
- **메타데이터 우선주의**: 벡터 유사도에 의존하기보다 인제스트 타임에 LLM으로 `category`/`tags`/`referenced_articles`를 풍부하게 태깅하고, 조회 타임에는 RDB 필터로 답한다.
- **단지 격리**: 한 단지의 tool 호출이 절대 다른 단지 데이터를 볼 수 없도록 tool 레벨에서 `complex_id`를 필수 파라미터로 강제한다.
- **원문 우선**: kor-legal-mcp와 동일하게 tool 응답은 원문 그대로, 요약·해석은 상위 LLM 책임.

## 리포지토리 구조
```
apt-domain-mcp/
├── AGENTS.md                        # 이 파일
├── pyproject.toml
├── src/apt_domain_mcp/
│   ├── server.py                    # FastMCP + Starlette (예정)
│   ├── config.py                    # Settings (env)
│   ├── db/                          # asyncpg 기반 repository 계층
│   ├── ingest/                      # 문서 → 구조화 파이프라인
│   ├── tools/                       # MCP tool handlers
│   ├── wiki/                        # LLM Wiki 생성기
│   └── models/                      # Pydantic 스키마
├── sql/
│   └── schema.sql                   # PostgreSQL 스키마
├── synthetic/                       # 가상 단지 합성 데이터
│   ├── regulation_v1.md             # 관리규약 v1 원본
│   ├── regulation_v2_diff.md        # v1 → v2 개정 diff (Phase 1)
│   └── meetings/                    # 회의록 합성본 (Phase 1)
├── scripts/
│   └── md_to_pdf.py                 # 마크다운 → PDF (reportlab)
└── tests/

# cross-cutting 문서(아키텍처/로드맵/합성단지 스펙)는 apt-legal-agent/docs/ 로 이관됨
```

## 커뮤니케이션 규칙
- 사용자와의 모든 소통은 한국어.
- 코드 주석은 영어 기본, 사용자 facing 메시지·tool 응답은 한국어.
- MCP Tool 응답은 `ensure_ascii=False`.

## Memory Sync

사용자는 여러 머신(사내 VDI + 집 WSL2)에서 이 리포를 작업하므로, Claude의 장기 기억은 **별도 private GitHub repo**(`~/.claude/global-memory/`, GitHub: `youngs75/claude-global-memory`)로 동기화됩니다. 마스터 규칙은 `~/.claude/CLAUDE.md`에 있습니다.

- **본 프로젝트가 참조할 글로벌 메모리 파일:**
  - `~/.claude/global-memory/apt-family.md` — 3-repo 구조·문서 허브·합성 데이터 원칙
  - `~/.claude/global-memory/portal-infra.md` — VDI egress 제한, 포털 수동 배포, endpoint 등록, LiteLLM Bedrock `json_object` 미지원, Web IDE 위임 패턴
- **세션 시작 시:** 위 파일들을 읽고(필요 시 `cd ~/.claude/global-memory && git pull --rebase -q` 선행) 적용
- **장기 기억 승격:** 프로젝트 로컬 auto memory(`~/.claude/projects/.../memory/`)는 scratch로만 사용. 다른 머신에서도 필요한 것, 프로젝트 철학·방향·경계, 여러 세션 재사용 교훈은 즉시 위 파일들 중 맞는 곳으로 승격
- **세션 종료 또는 `sync memory` 지시 시:** `cd ~/.claude/global-memory && git add -A && git commit -m "..." && git push`
- **금지:** 프로젝트 로컬 auto memory에만 중요한 장기 기억을 남기지 말 것 — 다른 머신에서 유실됩니다

## 세션 파일 명명 규칙
`.ai/sessions/session-YYYY-MM-DD-NNNN.md` 형식. kor-legal-mcp와 동일 규칙.

## Resume / Handoff 규칙
kor-legal-mcp `AGENTS.md`와 동일.
- `resume` / `이어서` → 최근 세션 파일 로드 후 브리핑
- `handoff` / `정리해줘` / `세션 종료` → 새 세션 파일 생성
- 기존 세션 파일은 절대 수정 금지

## 기술 스택
- **언어**: Python 3.12+
- **MCP**: `mcp` Python SDK + Starlette Streamable HTTP
- **DB**: PostgreSQL (asyncpg) — 코어 / Milvus — 회의록 벡터 (Phase 1 후반). 둘 다 배포 환경(AWS EKS)에 기존 제공.
- **PDF 파싱**: `pypdf` + `pdfplumber` (표 추출용)
- **PDF 생성**: `reportlab` (+ Windows `malgun.ttf` 임베딩)
- **스키마**: Pydantic v2
- **패키지 매니저**: `uv`
- **테스트**: pytest, pytest-asyncio

## 제공 예정 MCP Tools (Phase 1)
| Tool | 용도 |
|------|------|
| `search_regulation` | 단지 관리규약 조문 키워드 검색 (`complex_id` 필수) |
| `get_regulation_article` | 특정 조문 전문 조회 (개정 이력 포함) |
| `list_regulation_revisions` | 관리규약 개정 이력 목록 |
| `search_meeting_decisions` | 회의록 결정사항 검색 |
| `get_meeting_detail` | 특정 회의 안건·결정 상세 |
| `get_wiki_page` | 토픽 위키 페이지 조회 |
| `list_complexes` | 서버가 서빙 중인 단지 목록 (운영용) |

세부 IO 스키마는 `apt-legal-agent/docs/01_architecture.md` 참조.

## 환경 변수
```bash
DATABASE_URL=postgresql://...
MILVUS_URI=http://milvus:19530          # Phase 1 후반 활성화
MILVUS_COLLECTION=apt_meeting_chunks
SERVER_PORT=8002
WIKI_LLM_MODEL=claude-sonnet-4-6
WIKI_LLM_API_KEY=...

# LiteLLM proxy (포털 내부 기본 제공, 인제스트 타임 태거가 사용)
LITELLM_BASE_URL=...                    # 포털 주입 (또는 LITELLM_PROXY_URL)
LITELLM_API_KEY=...                     # 포털 주입 (또는 LITELLM_MASTER_KEY)
LITELLM_MODEL=us.anthropic.claude-sonnet-4-6   # Default, AWS Bedrock Claude Sonnet 4.6
LITELLM_USE_JSON_MODE=0                        # Default OFF. Bedrock Claude는 response_format json_object 파라미터가 빈 {}만 반환하는 이슈가 있어 기본 OFF. fence 제거 파서로 정상 처리.
LITELLM_TIMEOUT=120                            # Default 120s. Wiki 생성은 프롬프트가 길어 30s로는 부족. 타임아웃 시 1회 재시도(timeout 300s).

# Admin REST API (필수, 빈 문자열이면 admin 전면 거부)
ADMIN_API_KEY=
```

## 구현 시 유의사항
- **complex_id 강제**: 모든 tool의 첫 파라미터는 `complex_id`. tool 레벨에서 누락 시 `INVALID_PARAMS`.
- **개정 이력 보존**: 관리규약 조문은 update가 아닌 append(new version row). 이전 버전은 `is_current=false`로 유지.
- **HWP 파싱**: Phase 1에서 pyhwp 또는 HWP→PDF 변환 경유. 본 Phase는 PDF only.
- **원문 그대로**: tool 응답에서 truncate 금지 (kor-legal-mcp 원칙 승계).
- **면책**: 본 서버는 단지 내부 문서 조회 도구만 제공. 법적 해석은 상위 Agent 책임.

## 커밋 규칙
Conventional Commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
`.env`, `synthetic/*.pdf`, `data/`, `.ai/sessions/` 커밋 금지.
