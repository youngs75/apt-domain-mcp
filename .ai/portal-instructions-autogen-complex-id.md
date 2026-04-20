# apt-domain-mcp — POST /admin/api/complexes 서버 자동 ULID 발급 작업지시서 (Web IDE Claude 전용)

## 0. 배경 / 목적

현재 `POST /admin/api/complexes`는 `complex_id`를 **필수 입력**으로 요구한다. 그러나 ULID(26자)는 사람이 손으로 생성·입력할 값이 아니다. 서버가 자동 생성해서 응답에 돌려주는 게 올바른 API 형태다.

사용자 결정(2026-04-20): **"complex_id 자동생성. 이 값은 사람에게 큰 의미가 없다."**

본 지시서는 apt-domain-mcp 쪽만 건드린다. apt-web UI 측 변경(입력 필드 제거 등)은 후속 지시서.

## 1. 시작 컨텍스트

- 리포 경로: `apt-domain-mcp`
- 작업 전 `git pull --rebase`
- 관련 파일:
  - `src/apt_domain_mcp/admin/api.py::create_complex` — 수정 대상
  - `src/apt_domain_mcp/ingest/repository.py::new_ulid_like` — 기존 ULID 생성기 재활용
  - `src/apt_domain_mcp/ingest/repository.py::upsert_complex` — complex_id가 dict에 포함돼야 동작 (변경 불필요)
- 현재 동작:
  - `complex_id` 없으면 `400 INVALID_PARAMS`
  - 있으면 upsert (INSERT ON CONFLICT UPDATE)

## 2. 설계

- `complex_id`를 **선택 필드**로 전환
- 제공된 경우: 기존과 동일하게 upsert (상위 레이어가 특정 ID로 덮어쓰고 싶을 때)
- 생략된 경우: 서버가 `new_ulid_like()`로 새 ID 생성 후 INSERT (신규 단지 등록의 일반적 경로)
- 응답 body에 **최종 complex_id**를 항상 포함 (기존에도 있었음 — 확인만)
- 응답에 `generated: true|false` 플래그 추가 (클라이언트가 자동 생성 여부를 알 수 있도록)

## 3. 작업 단계

### 단계 1 — `admin/api.py::create_complex` 수정

`src/apt_domain_mcp/admin/api.py`의 `create_complex` 함수를 아래처럼 갱신:

```python
async def create_complex(request: Request) -> JSONResponse:
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        body = await request.json()
    except Exception:
        return _err("INVALID_JSON", "요청 본문이 유효한 JSON이 아닙니다.")

    name = body.get("name")
    if not name:
        return _err("INVALID_PARAMS", "name은 필수입니다.")

    # complex_id 자동 발급 (없으면 서버 ULID 생성)
    # complex_id auto-issue — server generates a ULID when omitted
    from ..ingest.repository import new_ulid_like, upsert_complex
    provided_id = body.get("complex_id")
    if provided_id:
        complex_id = provided_id
        generated = False
    else:
        complex_id = new_ulid_like()
        generated = True
    body["complex_id"] = complex_id

    try:
        async with db.acquire() as conn:
            await upsert_complex(conn, body)
        return _json(
            {
                "complex_id": complex_id,
                "generated": generated,
                "status": "created" if generated else "upserted",
            },
            201,
        )
    except Exception:
        logger.exception("create_complex failed")
        return _err("INTERNAL_ERROR", "단지 생성 실패", 500)
```

검증 포인트:
- `name`만 있으면 201 + 새 ULID 응답
- `complex_id` + `name` 둘 다 있으면 기존 행 upsert (`generated: false`)
- `complex_id`만 있고 `name` 없으면 400
- body가 비어있으면 400 (name 없음)

### 단계 2 — `AGENTS.md` REST 스펙 업데이트

`AGENTS.md`의 admin REST 스펙 섹션에서 `POST /admin/api/complexes` 부분을:

- `complex_id`를 **선택 필드**로 표기 (예: "complex_id (선택, 생략 시 서버가 ULID 생성)")
- 응답 예시에 `generated` 필드 추가:
  ```json
  {"complex_id": "01HZZABC...", "generated": true, "status": "created"}
  ```
- "자동 발급" 동작을 한 줄 요약으로 추가

### 단계 3 — `.env.example` / 설정 변경 없음

환경변수 추가·제거 없음. 이 단계는 skip.

### 단계 4 — 로컬 스모크

```bash
uv run uvicorn apt_domain_mcp.server:app --port 8002
```

별도 쉘:

```bash
API_KEY=dev-admin-key-abcdef  # 또는 로컬 .env의 실제 값
BASE=http://localhost:8002

# (A) 자동 생성 경로 — name만 전달
curl -sS -X POST "$BASE/admin/api/complexes" \
  -H "X-Admin-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"테스트 자동생성 단지","address":"서울 테스트로 1"}'
# 기대: 201 {"complex_id":"01HZZ...","generated":true,"status":"created"}

# (B) upsert 경로 — complex_id 포함
curl -sS -X POST "$BASE/admin/api/complexes" \
  -H "X-Admin-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"complex_id":"01HZZFIXEDTESTIDAAAAAAAAAA","name":"고정 ID 단지"}'
# 기대: 201 {"complex_id":"01HZZFIXEDTESTIDAAAAAAAAAA","generated":false,"status":"upserted"}

# (C) name 없음 — 400
curl -sS -i -X POST "$BASE/admin/api/complexes" \
  -H "X-Admin-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address":"x"}'
# 기대: 400 {"error":"INVALID_PARAMS","message":"name은 필수입니다."}

# (D) GET 목록 확인 — (A),(B)에서 생성한 단지가 보여야 함
curl -sS -H "X-Admin-API-Key: $API_KEY" "$BASE/admin/api/complexes" | jq '.complexes | length'
# 기대: 3 (기존 한빛마을 + (A) + (B))

# (E) 정리 — 테스트용으로 만든 단지는 DB에서 직접 DELETE
# (선택. Phase 2 범위에서는 DELETE 엔드포인트 없으므로 psql로 수동 정리)
```

### 단계 5 — 회귀 테스트

```bash
uv run pytest -q
```

기존 admin 관련 테스트가 `complex_id` 필수를 가정하고 있으면 **선택 필드로 전환 반영**. 없으면 skip.

### 단계 6 — 커밋 / 푸시

```bash
git add src/apt_domain_mcp/admin/api.py AGENTS.md .ai/
git commit -m "feat(admin): auto-generate complex_id on POST /admin/api/complexes

- complex_id is now optional. When omitted, server issues a new ULID
  via new_ulid_like() and returns it in the response with generated:true.
- Callers that want to upsert a specific ID keep the existing behavior
  (generated:false, status:upserted)."
git push origin main
```

### 단계 7 — 포털 수동 배포

- 포털 UI에서 수동 배포 트리거 (환경변수 변경 없음 → secret 재설정 불필요)
- 배포 완료 후 외부 스모크:

```bash
BASE=https://portal-serving-evangelist-1-mcp2-c01386b6.samsungsdscoe.com
API_KEY=<주입된 ADMIN_API_KEY>

# 자동 생성 경로 외부 검증
curl -sS -X POST "$BASE/admin/api/complexes" \
  -H "X-Admin-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"외부 스모크 단지"}'
# 기대: 201 {"complex_id":"01HZZ...","generated":true,"status":"created"}
```

### 단계 8 — 세션 기록

`.ai/sessions/session-2026-04-20-NNNN.md` (본 리포는 `.ai/sessions/` gitignore 유지). 기록:
- 이번 변경 요약 (complex_id 자동 발급)
- 최종 커밋 SHA
- 외부 스모크에서 생성된 ID 기록 + 필요 시 DB 정리 메모
- 다음 세션 ToDo (apt-web 측 UI 변경 — 후속 지시서)

## 4. 주의사항

- `new_ulid_like()` import는 **함수 안에서** (`from ..ingest.repository import ...`). 모듈 top-level로 올리는 리팩토링은 범위 밖.
- 기존 `upsert_complex`의 시그니처 변경 금지. dict에 complex_id를 담아 넘기는 방식 유지.
- **SQL injection 주의는 불필요** — asyncpg 파라미터 바인딩 사용 중. 단, `new_ulid_like()`가 안전한 문자만 생성하는지 구현 확인 (기존 생성기이므로 재검증만).
- 응답 body에 **항상 JSON**. 예외 발생 시에도 `_err()` 통해 JSON 반환 (기존 패턴 유지).
- AGENTS.md 업데이트 누락 금지 — 본 변경의 산출물 중 하나.

## 5. 보고 형식

각 단계 완료 시 1줄. 스모크 (A)~(D)는 HTTP status + generated 값 요약. 마지막에 `final commit SHA` + `outer smoke result` + `remaining blockers`.
