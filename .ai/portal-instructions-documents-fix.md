# apt-domain-mcp — list_documents 컬럼 버그 수정 배포 지시서 (Web IDE Claude 전용)

## 0. 배경 / 문제 진단

Web IDE 세션 2026-04-22 에서 apt-web `/admin` 의 문서 탭이 "문서 목록 조회 실패" 배너를 띄운다는 이슈가 보고되었다. 최초 가설은 "apt-domain-mcp 에 `GET /admin/api/complexes/{id}/documents` 엔드포인트가 미구현" 이었으나, VDI 측 조사에서 **오진** 으로 판명:

- 라우트 등록 ✅ `src/apt_domain_mcp/admin/api.py:375`
- 핸들러 함수 ✅ `list_documents` (`api.py:202-237`)
- **SQL 컬럼명 버그** ❌ — 핸들러가 `SELECT ... created_at FROM document` 쿼리하지만 `sql/schema.sql:33-47` 의 실제 컬럼명은 `uploaded_at` (`created_at` 컬럼은 존재하지 않음)

따라서 호출 시 PostgreSQL 이 `column "created_at" does not exist` 에러를 돌려주고 핸들러가 `INTERNAL_ERROR 500` 으로 응답 → apt-web 을 거쳐 UI 에 에러 배너 표시.

## 1. 시작 컨텍스트

- 리포: `apt-domain-mcp`
- 최신 커밋 (GitHub main): `16c6a7d fix(admin): correct column name created_at→uploaded_at in list_documents`
- 직전 배포 커밋: `69544dd docs(examples): add 4 synthetic complexes ...`
- 건드리지 말 것:
  - `sql/schema.sql` — 컬럼명 변경 없음 (기존 `uploaded_at` 유지)
  - 다른 admin 핸들러 (`list_regulations`, `list_meetings`) — 자체 스키마 정합하므로 무관
  - apt-web 쪽 변경 없음 — catch-all relay 가 투명 포워딩
- 마이그레이션 없음, 환경변수 변경 없음

## 2. 변경 요약 (`16c6a7d` 내용)

### 2.1. 수정 파일

| 경로 | 변경 |
|---|---|
| `src/apt_domain_mcp/admin/api.py` | `list_documents` 핸들러의 SELECT/ORDER BY 컬럼명 `created_at` → `uploaded_at`. 응답 매핑에서 `r["uploaded_at"].isoformat()` 을 **JSON 응답 키 `created_at`** 으로 직렬화 (admin UI 계약 호환 유지). 주석 영/한 병기. |
| `tests/test_admin_api.py` | 3건 추가 — `test_list_documents_no_db_returns_503`, `test_list_documents_complex_not_found_returns_404`, `test_list_documents_returns_rows`. 마지막 케이스는 응답 직렬화 검증 + SQL 쿼리에 `uploaded_at` 이 있고 `created_at` 이 없음을 확인. |

### 2.2. API 계약 (변경 없음 — admin UI 그대로 호환)

```
GET /admin/api/complexes/{id}/documents
X-Admin-API-Key: <key>

→ 200 OK
{
  "complex_id": "01HXX...",
  "count": N,
  "documents": [
    {
      "document_id": "...",
      "kind": "regulation" | "meeting" | "regulation-diff" | ...,
      "title": "...",
      "source_path": "...",
      "sha256": "...",
      "pages": 84 | null,
      "created_at": "2026-04-14T12:00:00+00:00"   ← key name 그대로 유지
    },
    ...
  ]
}
```

응답 키 `created_at` 은 admin UI (`apt-web/src/apt_web/static/admin.html`) 가 참조하는 필드명과 일치하므로 프런트 변경 불필요.

### 2.3. 테스트 결과 (VDI 로컬 기준)

```
uv run pytest -q
49 passed in 2.10s
```

- 기존 46 테스트 회귀 없음
- 신규 3 테스트 (documents) 통과

## 3. Web IDE 에서 할 일

### 단계 1 — GitHub 최신 반영

```bash
git fetch origin
git pull --rebase origin main
git log --oneline -3    # 최상단이 16c6a7d 여야 함
```

### 단계 2 — 로컬 스모크 (선택, 권장)

Web IDE 환경은 포털 DB egress 가 열려 있으므로 로컬에서 실DB 바라보고 검증 가능:

```bash
export DATABASE_URL=<포털 PostgreSQL DSN>
export ADMIN_API_KEY=<포털 admin key>
uv run pytest -q                                    # 49/49 green 확인
uv run uvicorn apt_domain_mcp.server:app --port 8000
```

실서버 DB 에 연결된 상태에서:

```bash
# 한빛마을 complex_id 가 이미 알려져 있으므로 직접 호출 가능
curl -sS -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  "http://localhost:8000/admin/api/complexes/01HXXSOL0000000000000000AA/documents" | jq .
```

기대: `{"complex_id": "01HXX...", "count": N, "documents": [...]}` — 500 에러가 안 나오고 `created_at` 필드가 ISO 타임스탬프로 채워져 있어야 함.

### 단계 3 — GitLab push

```bash
git remote -v
git push <gitlab-remote> main
```

### 단계 4 — 포털 수동 배포

- Dockerfile·pyproject.toml 변경 없음 → 빌드 리스크 최소
- 마이그레이션 없음 → DB 무손실
- 포털 UI 에서 apt-domain-mcp 앱 재배포
- 배포 완료 후 `/healthz` 200 OK 확인

## 4. 외부 E2E 검증 시나리오

배포 endpoint: `https://portal-serving-evangelist-1-mcp2-c01386b6.samsungsdscoe.com`

### 4.1. 직접 API 호출 (curl)

```bash
curl -sS -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/01HXXSOL0000000000000000AA/documents" | jq .
```

기대: 200 OK + `documents[]` 배열. `created_at` 필드가 null 이 아닌 ISO 8601 타임스탬프.

### 4.2. admin UI 에서 검증

배포 endpoint: `https://portal-serving-evangelist-1-web-975174f8.samsungsdscoe.com/admin`

| # | 스텝 | 기대 |
|---|---|---|
| 1 | `/admin` 로그인 | 사이드바에 "한빛마을 새솔아파트" 표시 |
| 2 | 한빛마을 클릭 → "문서" 탭 | **에러 배너 없음**. 문서 테이블에 regulation(v1/v2/v3) + meeting(N건) 행 표시 |
| 3 | 각 행 확인 | `kind`, `title`, `pages`, `created_at` 정상 표시 (빈 값 없음) |
| 4 | 하늘 세종 단지 (sessions-2026-04-22 bulk ingest 로 등록된 경우) → "문서" 탭 | 7 문서 (regulation v1 + diff v2 + diff v3 + meetings 3 + complex.json 은 document 테이블 대상 아님 — regulation 3개 + meeting 3개 = 6개 가능) 정상 조회 |
| 5 | (회귀) "관리규약" 탭·"회의록" 탭 | 기존 기능 그대로 |

### 4.3. 체크리스트

- [ ] `/documents` 직접 curl 200 OK + `created_at` 필드 채워짐
- [ ] 한빛마을 문서 탭 에러 배너 해제
- [ ] 하늘 세종 문서 탭 (해당 단지 존재 시) 에러 배너 해제
- [ ] 관리규약·회의록 탭 회귀 없음

## 5. 주의사항 / 함정

- **스키마 컬럼은 `uploaded_at` 유지**: 이번 fix 는 핸들러를 실제 스키마에 맞춘 것. 스키마를 `created_at` 으로 바꾸는 방향은 데이터 마이그레이션·다른 사용처 확인이 필요해 **범위 외**. 응답 JSON 키는 UI 호환 위해 `created_at` 유지.
- **catch-all relay**: apt-web 은 변경 없음. apt-domain-mcp 배포 직후 UI 에서 즉시 효과 확인 가능 (브라우저 새로고침 필요할 수 있음 — admin.html 은 이미 cache-control no-cache 설정됨).
- **force push 금지**.
- **X-Admin-API-Key**·**DATABASE_URL** 등 세션 기록 금지.

## 6. 보고 형식

1. GitHub pull 후 최상단 커밋 SHA 확인 결과
2. (선택) 로컬 스모크 결과 — `pytest 49/49` + curl 문서 조회 JSON 샘플
3. GitLab push 결과 (remote, branch)
4. 포털 빌드 완료 시각 + `/healthz` 응답
5. §4 체크리스트 4항목 결과
6. 스크린샷 1~2장 (한빛마을 또는 하늘 세종 문서 탭 정상 표시)
7. final commit SHA (apt-domain-mcp main 최상단)
8. remaining blockers (있으면)
