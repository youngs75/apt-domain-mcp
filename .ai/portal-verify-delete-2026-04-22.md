# apt-domain-mcp — DELETE 엔드포인트 배포 검증 지시서 (Web IDE Claude 전용, 2026-04-22)

## 0. 시작 컨텍스트

VDI 측에서 `DELETE /admin/api/complexes/{id}` 엔드포인트를 구현해 GitHub main 에 커밋
`a1b2221` 로 푸시했고, 사용자가 방금 포털에 수동 배포했다. 본 세션은 **배포 검증 + 포털
DB 테스트 단지 정리 + 세션 기록** 전담이다. **코드 수정은 하지 말 것.**

- 기대 커밋: `git log --oneline -3` 결과에 `a1b2221 feat(admin): DELETE ...` 포함
- 작업 전 `git fetch --all && git pull --rebase` 선행. 만약 충돌이 있다면 **멈추고 사용자에게 보고**.
  (apt-domain-mcp 는 GitHub↔GitLab 양방향 sync 이슈 전례가 있으니 divergent 시 즉시 알림.)
- 배포 대상 endpoint: `https://portal-serving-evangelist-1-mcp2-c01386b6.samsungsdscoe.com`
- `ADMIN_API_KEY` 는 포털 주입 env 로 이미 세팅됨. 출력에 절대 노출 금지.
- 상세 설계 배경은 `.ai/portal-instructions-delete-complex.md` 참조 (본 지시서는 **검증 전용** 축약본).

---

## 1. 배포 반영 확인

```bash
BASE=https://portal-serving-evangelist-1-mcp2-c01386b6.samsungsdscoe.com
KEY=$ADMIN_API_KEY

# /healthz — 200 + postgres:ok 확인
curl -sS $BASE/healthz | jq .

# DELETE 엔드포인트가 실제 바인딩되어 있는지: confirm_name 없이 호출 → 400 INVALID_PARAMS 기대
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/NONEXISTENT" | head -5
# 기대: HTTP/1.1 400, body {"error":"INVALID_PARAMS",...}
# 만약 405 Method Not Allowed 가 나오면 배포가 아직 반영 안 된 것 → 사용자에게 알림 후 멈춤.
```

---

## 2. 엔드포인트 스모크 (에러 경로 4종 + 정상 경로 1종)

```bash
# (A) 빈 테스트 단지 생성 — 자동 발급 ID 받기
NEW=$(curl -sS -X POST -H "X-Admin-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"delete-smoke-target","address":"test-addr"}' \
  $BASE/admin/api/complexes | jq -r '.complex_id')
echo "created: $NEW"   # 01HZZ... 또는 0019... 형태의 ULID-like 값

# (B) confirm_name 누락 → 400
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/$NEW" | head -3
# 기대: HTTP/1.1 400, error=INVALID_PARAMS

# (C) 이름 불일치 → 409
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/$NEW?confirm_name=wrong-name" | head -3
# 기대: HTTP/1.1 409, error=NAME_MISMATCH

# (D) 존재하지 않는 ID → 404
curl -sS -i -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/01HZZ000000000000000000000?confirm_name=x" | head -3
# 기대: HTTP/1.1 404, error=COMPLEX_NOT_FOUND

# (E) 정상 삭제 → 200
curl -sS -X DELETE -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/$NEW?confirm_name=delete-smoke-target" | jq .
# 기대: {"complex_id":"...","status":"deleted"}
```

---

## 3. 누적 테스트 단지 4건 정리

2026-04-22 VDI 측 실측 결과, 아래 4건이 DB 에 누적되어 있다. **한빛마을
(`01HXXSOL0000000000000000AA`) 은 절대 건드리지 말 것 — 시연 시드.**

| complex_id | name (DB 기준, 2026-04-22 확인) |
|---|---|
| 0019DA90A8DFAB0F6D831B410F | 테스트 자동생성 단지 |
| 0019DA90EB684548C227471887 | 테스트 자동생성 단지 |
| 0019DA915AAD85DD199340D7C4 | smoke-autogen |
| 01HZZFIXEDTESTIDAAAAAAAAAA | 고정 ID 단지 |

이름은 DB 실제값을 다시 읽어 URL-encode 해서 전달하도록 구현하는 게 안전하다:

```bash
for ID in 0019DA90A8DFAB0F6D831B410F 0019DA90EB684548C227471887 \
          0019DA915AAD85DD199340D7C4 01HZZFIXEDTESTIDAAAAAAAAAA; do
  NAME=$(curl -sS -H "X-Admin-API-Key: $KEY" $BASE/admin/api/complexes \
          | jq -r ".complexes[] | select(.complex_id==\"$ID\") | .name")
  if [ -z "$NAME" ]; then
    echo "$ID already absent"
    continue
  fi
  ENC=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$NAME")
  echo "deleting $ID ($NAME)"
  curl -sS -X DELETE -H "X-Admin-API-Key: $KEY" \
    "$BASE/admin/api/complexes/$ID?confirm_name=$ENC" | jq .
done
```

---

## 4. 최종 상태 확인

```bash
curl -sS -H "X-Admin-API-Key: $KEY" $BASE/admin/api/complexes \
  | jq '{count, names: [.complexes[].name]}'
# 기대: {"count":1,"names":["한빛마을 새솔아파트"]}

# 한빛마을 종속 데이터 회귀 — regulations 카운트가 그대로인지
curl -sS -H "X-Admin-API-Key: $KEY" \
  "$BASE/admin/api/complexes/01HXXSOL0000000000000000AA/regulations" \
  | jq '.count'
# 기대: 3 (또는 현행 시드 값)
```

---

## 5. 세션 파일 생성 + 커밋/푸시

- **파일명**: `.ai/sessions/session-2026-04-22-0001.md` (오늘 날짜, apt-domain-mcp 리포 기준 첫 세션이면 0001)
- **내용**: 시작 컨텍스트, 단계 1~4 결과 (HTTP status + key JSON 필드), 남은 이슈, 다음 세션 ToDo
- **민감 정보 기록 금지**: API Key, 비밀번호는 `<secret-from-env>` 플레이스홀더로만 참조
- **커밋 메시지**: `chore: verify DELETE endpoint + cleanup 4 test complexes (2026-04-22-0001)`
- **푸시**: GitLab(배포용)과 GitHub(VDI 동기화용) **둘 다**. `git remote -v` 로 확인 후 각각 명시적으로 push.

---

## 6. 주의사항

- **한빛마을(`01HXXSOL0000000000000000AA`) 삭제 금지** — 시연 시드. 단계 3 루프에 들어 있지 않지만 재확인.
- **`confirm_name` 은 URL-encoded 문자열**. 한글 단지명 그대로 전달하면 400/ 409 오검지 가능.
- **idempotency 아님**: 두 번째 DELETE 는 404. 오히려 "이미 없어졌음" 으로 해석.
- **soft delete 아님**: 삭제는 되돌릴 수 없음. 시드 복원이 필요하면 `seed.py` 사용.
- **force push 금지**.

---

## 7. 보고 형식

각 단계 1줄씩. 단계 2 의 (A)~(E) 는 HTTP status + 주요 JSON 필드. 마지막에:
- 최종 단지 count + names 목록
- 세션 파일 경로 + 커밋 SHA
- remaining blockers (있으면)
