# apt-domain-mcp — DELETE /admin/api/complexes/{id} 추가 작업지시서 (Web IDE Claude 전용)

## 0. 배경 / 목적

현재 admin REST는 GET/POST만 지원하고 **DELETE가 없다**. 그 결과:

- 테스트로 만든 단지가 DB에 누적 (현재 `smoke-autogen`, `테스트 자동생성 단지` 등 4건 누적)
- 시연 환경 정리를 매번 psql로 수작업

본 지시서는 `DELETE /admin/api/complexes/{id}` 엔드포인트를 추가한다. 하드 삭제(hard delete)다. 안전장치는 **호출자가 `confirm_name`을 쿼리/바디로 함께 보내 실제 단지명과 일치할 때만 실행**하도록 한다. apt-web UI는 이 값을 "단지명을 정확히 타이핑하세요" UX로 받아 전달한다 (별도 지시서).

## 1. 시작 컨텍스트

- 리포 경로: `apt-domain-mcp`
- 작업 전 `git pull --rebase`
- 현재 커밋은 `8261532` (complex_id 자동 발급) 이후. 후속 커밋이 있다면 그 위에서 작업.
- 관련 파일:
  - `src/apt_domain_mcp/admin/api.py` — 핸들러 + `api_routes`
  - `sql/schema.sql` — FK 제약 확인용 (수정 없음)
  - `AGENTS.md` — REST 스펙 갱신

## 2. 설계

### 2.1. 엔드포인트

```
DELETE /admin/api/complexes/{id}
Headers:
  X-Admin-API-Key: <ADMIN_API_KEY>
Query:
  confirm_name=<정확한 단지 이름 URL-encoded>   # 필수 (안전장치)
```

**Response 200**:
```json
{"complex_id": "01HXX...", "status": "deleted"}
```

**Errors**:
- `400 INVALID_PARAMS` — `confirm_name` 누락
- `404 COMPLEX_NOT_FOUND` — 해당 ID 단지 없음
- `409 NAME_MISMATCH` — `confirm_name`이 DB의 실제 이름과 불일치 (대소문자·공백 민감)
- `500 INTERNAL_ERROR` — 기타

모든 응답은 JSON body. 플레인 텍스트 금지.

### 2.2. FK 제약 상황 (schema.sql 기준)

대부분 `ON DELETE CASCADE`:
- `regulation_version → complex` CASCADE
- `regulation_article → regulation_version` CASCADE
- `meeting → complex` CASCADE
- `decision → meeting` CASCADE
- `document → complex` CASCADE
- `wiki_page → complex` CASCADE (확인 필요)

**예외 1건**: `regulation_diff`의 FK는 ON DELETE 절이 **누락**되어 PostgreSQL 기본 `NO ACTION` 적용. `regulation_version`이 CASCADE로 삭제될 때 `regulation_diff`가 남아 있으면 **체인이 막혀 에러**.

### 2.3. 해결 방식 — 트랜잭션에서 순차 삭제 (A안)

단일 `DELETE FROM complex WHERE complex_id=$1`로 처리하지 말고, 트랜잭션 안에서 `regulation_diff`를 먼저 정리한 뒤 `complex`를 삭제한다:

```python
async with conn.transaction():
    await conn.execute("DELETE FROM regulation_diff WHERE complex_id = $1", complex_id)
    await conn.execute("DELETE FROM complex WHERE complex_id = $1", complex_id)
    # 나머지는 CASCADE로 자동 정리
```

향후 깔끔한 해결은 schema migration으로 `regulation_diff` FK에도 `ON DELETE CASCADE` 추가. 본 세션에서는 scope 밖 — 본 지시서 부록 B에 migration SQL만 메모.

### 2.4. 안전장치 `confirm_name`

- `request.query_params.get("confirm_name")`으로 읽기
- 비어 있거나 없으면 400
- DB의 `complex.name`과 strict 비교 (trim 없이 완전 일치). 다르면 409
- 이 값은 apt-web UI에서 사용자가 직접 타이핑한 것을 URL-encoded로 전달

## 3. 작업 단계

### 단계 1 — `admin/api.py`에 `delete_complex` 핸들러 추가

```python
# --------------------------------------------------------------------------
# DELETE /complexes/{id}?confirm_name=<name>
# --------------------------------------------------------------------------
async def delete_complex(request: Request) -> JSONResponse:
    complex_id = request.path_params["id"]
    confirm_name = request.query_params.get("confirm_name", "")
    if not confirm_name:
        return _err(
            "INVALID_PARAMS",
            "confirm_name 쿼리 파라미터는 필수입니다. 삭제할 단지 이름을 정확히 전달하세요.",
        )
    if db.get_pool() is None:
        return _err("DB_NOT_CONFIGURED", "DATABASE_URL이 설정되지 않았습니다.", 503)
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name FROM complex WHERE complex_id = $1",
                complex_id,
            )
            if row is None:
                return _err("COMPLEX_NOT_FOUND", f"등록되지 않은 단지: {complex_id}", 404)
            if row["name"] != confirm_name:
                return _err(
                    "NAME_MISMATCH",
                    f"confirm_name이 단지 이름과 일치하지 않습니다. DB 값: {row['name']!r}",
                    409,
                )
            async with conn.transaction():
                # regulation_diff는 FK에 ON DELETE 절이 없어 cascade chain을 막음.
                # 먼저 명시적으로 정리한 뒤 complex를 삭제하면 나머지는 CASCADE로 흐른다.
                await conn.execute(
                    "DELETE FROM regulation_diff WHERE complex_id = $1",
                    complex_id,
                )
                await conn.execute(
                    "DELETE FROM complex WHERE complex_id = $1",
                    complex_id,
                )
        return _json({"complex_id": complex_id, "status": "deleted"})
    except Exception:
        logger.exception("delete_complex failed for %s", complex_id)
        return _err("INTERNAL_ERROR", "단지 삭제 실패", 500)
```

### 단계 2 — `api_routes`에 등록

```python
api_routes: list[Route] = [
    Route("/complexes", list_complexes, methods=["GET"]),
    Route("/complexes", create_complex, methods=["POST"]),
    Route("/complexes/{id}", delete_complex, methods=["DELETE"]),   # 신규
    Route("/complexes/{id}/regulations", list_regulations, methods=["GET"]),
    Route("/complexes/{id}/meetings", list_meetings, methods=["GET"]),
    Route("/complexes/{id}/documents", list_documents, methods=["GET"]),
    Route("/complexes/{id}/ingest", ingest, methods=["POST"]),
]
```

`DELETE` 라우트가 `/complexes/{id}` 경로를 차지하므로, 향후 `GET /complexes/{id}` (단건 조회)를 추가할 때 충돌 없이 공존 가능 (Starlette는 HTTP method로 분기).

### 단계 3 — `AGENTS.md` REST 스펙 업데이트

admin REST 스펙 섹션에 다음을 추가:

```markdown
### DELETE /admin/api/complexes/{id}

단지를 **하드 삭제**한다. regulation_version·regulation_article·regulation_diff·
meeting·decision·document·wiki_page 등 종속 데이터가 함께 제거된다.

- Query: `confirm_name=<단지 이름 URL-encoded>` (필수, 안전장치)
- 200 `{"complex_id": "01HXX...", "status": "deleted"}`
- 400 INVALID_PARAMS (confirm_name 누락)
- 404 COMPLEX_NOT_FOUND
- 409 NAME_MISMATCH (DB의 name과 불일치)
- 503 DB_NOT_CONFIGURED
- 500 INTERNAL_ERROR
```

### 단계 4 — 로컬 스모크

```bash
BASE=http://localhost:8002
KEY=<로컬 .env의 ADMIN_API_KEY>

# 사전: 시드 확인 (한빛마을은 남겨둬야 함)
curl -sS -H "X-Admin-API-Key: $KEY" $BASE/admin/api/complexes | jq '.count'

# (A) 테스트용 단지 생성
NEW=$(curl -sS -X POST -H "X-Admin-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"delete-target","address":"test"}' \
  $BASE/admin/api/complexes | jq -r '.complex_id')
echo "created: $NEW"

# (B) confirm_name 누락 → 400
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" "$BASE/admin/api/complexes/$NEW" | head -1
# 기대: HTTP/1.1 400

# (C) 이름 불일치 → 409
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/$NEW?confirm_name=wrong-name" | head -1
# 기대: HTTP/1.1 409

# (D) 존재하지 않는 ID → 404
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/01HZZ000000000000000000000?confirm_name=x" | head -1
# 기대: HTTP/1.1 404

# (E) 정상 삭제 → 200
curl -sS -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/$NEW?confirm_name=delete-target" | jq .
# 기대: {"complex_id":"...","status":"deleted"}

# (F) 한빛마을 데이터 상태 회귀 (regulations·meetings·documents가 그대로인지)
curl -sS -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/01HXXSOL0000000000000000AA/regulations" | jq '.count'
# 기대: 기존 count (3 또는 현행값)
```

### 단계 5 — regulation_diff 경로 회귀 테스트

한빛마을은 v2/v3 diff가 존재한다. 테스트용으로 **복제한 단지**를 만든 뒤 삭제해 regulation_diff 경로가 막히지 않는지 확인. 복잡하면 다음 대안:

- (대안) 로컬에서 한 번 한빛마을 복제본을 시드 → 삭제 테스트 → 재시드. 운영 DB 건드리지 말 것.
- (대안 2) 스모크 단계에서는 **diff 없는 테스트 단지**로 삭제만 검증. Web IDE 외부 스모크 전, 사용자가 실제 단지 시나리오를 확인할 때 이 회귀를 다시 시도.

### 단계 6 — pytest

```bash
uv run pytest -q
```

본 변경은 추가만. 회귀 위험 낮음. 필요하면 `tests/test_admin_api.py`에 삭제 시나리오 1-2건 추가 (기존 테스트 패턴 따름).

### 단계 7 — 커밋 / 푸시

```bash
git add src/apt_domain_mcp/admin/api.py AGENTS.md .ai/
git commit -m "feat(admin): DELETE /admin/api/complexes/{id} with confirm_name guard

- Hard delete: cascades regulation_version/article/meeting/decision/
  document/wiki_page via FK ON DELETE CASCADE.
- Explicitly clears regulation_diff first (its FK lacks ON DELETE
  CASCADE — schema fix is out of scope for this change).
- Requires ?confirm_name=<exact name> to match the DB row; returns
  409 NAME_MISMATCH otherwise. 400 on missing param, 404 on unknown id.
- All error responses are JSON bodies."
git push origin main
```

### 단계 8 — 포털 수동 배포 + 외부 스모크

배포 후:

```bash
BASE=https://portal-serving-evangelist-1-mcp2-c01386b6.samsungsdscoe.com
KEY=<주입된 ADMIN_API_KEY>

# DB에 남아 있는 테스트 단지(외부 스모크 잔여)를 정리
for ID in 0019DA90A8DFAB0F6D831B410F 0019DA90EB684548C227471887 \
          0019DA915AAD85DD199340D7C4 01HZZFIXEDTESTIDAAAAAAAAAA; do
  NAME=$(curl -sS -H "X-Admin-API-Key: $KEY" $BASE/admin/api/complexes \
          | jq -r ".complexes[] | select(.complex_id==\"$ID\") | .name")
  if [ -z "$NAME" ]; then
    echo "$ID already absent"
    continue
  fi
  echo "deleting $ID ($NAME)"
  curl -sS -X DELETE -H "X-Admin-API-Key: $KEY" \
    "$BASE/admin/api/complexes/$ID?confirm_name=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$NAME")"
  echo
done

# 정리 후 count 확인 — 한빛마을 1건만 남아야 함
curl -sS -H "X-Admin-API-Key: $KEY" $BASE/admin/api/complexes \
  | jq '{count, names: [.complexes[].name]}'
# 기대: {"count":1,"names":["한빛마을 새솔아파트"]}
```

### 단계 9 — 세션 기록

`.ai/sessions/session-2026-04-20-NNNN.md`에 아래 기록:
- DELETE 엔드포인트 추가 요약
- 최종 커밋 SHA
- 외부 스모크로 정리한 테스트 단지 4건 목록
- `regulation_diff` FK 기본값 문제는 본 세션에서 우회(트랜잭션 순차 삭제)만 함. 향후 schema migration 후보는 부록 B 참조.

## 4. 주의사항

- **`confirm_name`은 URL-encoded**. Web IDE 로컬 스모크에서 한글 단지 이름을 시험할 때 `curl --data-urlencode` 또는 Python `urllib.parse.quote` 사용 권장.
- **한빛마을(`01HXXSOL...`) 삭제 금지** — 시연용 시드. 실수 방지 위해 단계 8의 루프에 한빛마을 ID를 넣지 말 것.
- **admin API Key**는 기존 `ADMIN_API_KEY`를 그대로 사용 (신규 env 없음).
- **idempotency 아님** — 두 번째 DELETE는 404 반환. 클라이언트가 재시도할 때 이를 성공으로 간주해도 되도록 apt-web UI는 404를 "이미 삭제됨" 으로 해석 처리 (후속 지시서).
- **soft delete 아님** — 삭제는 되돌릴 수 없다. 시연 전 시드 복원 스크립트가 필요하면 `seed.py` 사용.

## 5. 보고 형식

각 단계 1줄. 단계 4의 (A)~(F)는 HTTP status + key 응답. 최종:
- final commit SHA
- 외부 스모크 정리 결과 (삭제된 ID 개수, 남은 count)
- remaining blockers (있으면)

## 부록 A — apt-web 측 후속

`apt-web`에는 별도 지시서(`apt-web/.ai/portal-instructions-admin-ui-expand.md`의 "단지 삭제 UX" 섹션)로 UI 삭제 버튼을 추가한다. 본 리포는 백엔드 준비로 종료.

## 부록 B — schema cleanliness 후보 (본 세션 scope 밖)

`regulation_diff`의 FK에 `ON DELETE CASCADE`를 붙이면 DELETE 핸들러에서 명시적 삭제 라인을 제거할 수 있다. Migration SQL 초안:

```sql
ALTER TABLE regulation_diff
  DROP CONSTRAINT regulation_diff_complex_id_from_version_fkey,
  DROP CONSTRAINT regulation_diff_complex_id_to_version_fkey;

ALTER TABLE regulation_diff
  ADD FOREIGN KEY (complex_id, from_version)
      REFERENCES regulation_version(complex_id, version) ON DELETE CASCADE,
  ADD FOREIGN KEY (complex_id, to_version)
      REFERENCES regulation_version(complex_id, version) ON DELETE CASCADE;
```

실제 constraint 이름은 `\d regulation_diff` 또는 `pg_constraint` 쿼리로 확인 필요. 본 세션은 코드 레벨 우회로 충분 — migration은 Phase 3 이후 합류.
