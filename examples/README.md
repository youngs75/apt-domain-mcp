# examples — 가상 단지 4벌 (ingest 테스트·멀티테넌시 실증용)

`apt-domain-mcp` 파이프라인과 `apt-legal-agent` 의 멀티테넌시 기능을 검증하기 위한 **합성 가상 단지 4벌**. 기존 파일럿 단지인 "한빛마을 새솔아파트"(`synthetic/`)와 같은 구조를 따르되, 관리방식·규모·연식·운영 갈등이 서로 다르게 설계되어 있다.

**단지명 1건 주의**: `boram_bucheon/` 의 "부천 보람마을 아주 아파트" 는 사용자 실거주 단지명을 반영했다. 다만 **주소·세대수·조문·회의록·결정사항 등 세부 데이터는 100% 합성**이며, 실제 부천 보람마을 아주 아파트의 규약·운영과는 무관하다. 나머지 3 단지는 단지명까지 전부 합성이다. 어떤 단지에 대해서도 실존 자료로 사용되어서는 아니 된다.

## 1. 단지 구성

| 폴더 | 단지명 | 특성 | 관리방식 | 규모 | 사용검사 | 대표 운영 갈등 |
|---|---|---|---|---|---|---|
| `solhyang_songpa/` | 송파 솔향마을 아파트 | 노후·자치 | **자치관리** | 480세대·6동 | 1998-05-20 | 재활용품 수익 배분, 재건축 의견수렴 |
| `haneul_sejong/` | 세종 하늘마을 아파트 | 신축·스마트 | 위탁관리 | 2,408세대·28동 | 2021-11-30 | EV 충전·스마트홈 원격유지·데이터 프라이버시 |
| `geumbit_bupyeong/` | 부평 금빛마을 아파트 | 중규모·전형 갈등 | 위탁관리 | 812세대·10동 | 2005-07-25 | 흡연 분쟁·경비 무인화·리모델링 추진위 |
| `boram_bucheon/` | 부천 보람마을 아주 아파트 ⚠ | 중규모·점진적 스마트화 | 위탁관리 | 1,024세대·12동 | 2003-04-15 | 자전거 도난·무인택배·공동돌봄·공유 세탁실 |

⚠ 실거주 단지명만 사용, 세부 데이터는 전부 합성 (§0 고지 참조).

## 2. 파일 구조 (단지별 동일)

```
<단지폴더>/
├── complex.json              # 단지 메타 (POST /admin/api/complexes body 규격)
├── regulation_v1.md          # 관리규약 v1 (제정본, 10장 84조)
├── regulation_v2_diff.md     # v1 → v2 개정 diff (2~3 조문 + 1 신설)
├── regulation_v3_diff.md     # v2 → v3 개정 diff (2~3 조문 + 1 신설)
└── meetings/                 # 회의록 3건 (정기 2 / 임시 1)
    ├── <date>_regular.md
    ├── <date>_extraordinary.md
    └── <date>_regular.md
```

규약 개정은 기존 조문을 유지한 채 **신설 조문 번호를 제85조·제86조·... 뒤에 append** 하는 방식을 따른다 ("조의2" 회피, `~/.claude/global-memory/apt-family.md` 의 합성 데이터 원칙).

## 3. 로드 방법

### 3-1. admin REST API 로 개별 인제스트 (권장)

```bash
BASE=https://portal-serving-evangelist-1-mcp2-c01386b6.samsungsdscoe.com
KEY=$ADMIN_API_KEY

# 1) 단지 생성 (complex.json 을 그대로 POST)
COMPLEX_ID=$(jq -c . examples/solhyang_songpa/complex.json \
  | curl -sS -X POST -H "X-Admin-API-Key: $KEY" -H "Content-Type: application/json" \
      -d @- $BASE/admin/api/complexes \
  | jq -r '.complex_id')
echo "solhyang: $COMPLEX_ID"

# 2) 관리규약 v1 업로드 (make_current=true)
curl -sS -X POST -H "X-Admin-API-Key: $KEY" \
  -F "kind=regulation" -F "make_current=true" \
  -F "file=@examples/solhyang_songpa/regulation_v1.md" \
  "$BASE/admin/api/complexes/$COMPLEX_ID/ingest"

# 3) v2, v3 diff 순차 적용 (kind=regulation-diff, make_current=true 는 마지막만)
curl -sS -X POST -H "X-Admin-API-Key: $KEY" \
  -F "kind=regulation-diff" -F "make_current=false" \
  -F "file=@examples/solhyang_songpa/regulation_v2_diff.md" \
  "$BASE/admin/api/complexes/$COMPLEX_ID/ingest"

curl -sS -X POST -H "X-Admin-API-Key: $KEY" \
  -F "kind=regulation-diff" -F "make_current=true" \
  -F "file=@examples/solhyang_songpa/regulation_v3_diff.md" \
  "$BASE/admin/api/complexes/$COMPLEX_ID/ingest"

# 4) 회의록 3건 업로드
for F in examples/solhyang_songpa/meetings/*.md; do
  curl -sS -X POST -H "X-Admin-API-Key: $KEY" \
    -F "kind=meeting" \
    -F "file=@$F" \
    "$BASE/admin/api/complexes/$COMPLEX_ID/ingest"
done
```

나머지 두 단지(`haneul_sejong/`, `geumbit_bupyeong/`)도 같은 절차. 정리는 `DELETE /admin/api/complexes/{id}?confirm_name=<name>`.

### 3-2. 일괄 시드 스크립트 (선택)

`scripts/seed_examples.py` 를 추가할 경우 다음과 같은 반복만 돌려도 된다 (아직 미구현, 필요 시 Phase 3 에서 추가):

```python
for ex in Path("examples").iterdir():
    if not (ex / "complex.json").exists():
        continue
    upsert_complex(json.loads((ex/"complex.json").read_text()))
    ingest_regulation(ex/"regulation_v1.md", make_current=True)
    for diff in sorted(ex.glob("regulation_v*_diff.md")):
        ingest_regulation_diff(diff)
    for meet in sorted((ex/"meetings").glob("*.md")):
        ingest_meeting(meet)
```

## 4. 설계 근거

- **한빛마을과의 대조성**: 관리방식(자치 vs 위탁), 규모(소·중·대), 연식(신축·노후·중간), 시설 성격(스마트·전형)을 조합해 **멀티테넌시 시연 시 각 단지 고유 맥락이 드러나도록** 구성.
- **규약 본문의 80~90% 는 서울시 공동주택 관리규약 준칙과 동일**: 공동주택관리법·시행령 공통 항목은 공통. 단지 identity 가 드러나는 15~20 개 조문(제13조 대표회의 구성, 제29조 관리방식, 제41조 관리비 부과, 제55조 주차장, 제58조 난방, 제59~64조 주민공동시설 등)만 차별화.
- **개정 이력 스토리**: 각 단지의 실제 운영 갈등(재활용 수익, EV 충전, 흡연 분쟁 등)이 v2/v3 개정 사유에 드러나도록 배치. 회의록 안건과 관련 조문(`관련 조문: 제NN조`) 으로 cross-link.

## 5. 주의

- **한빛마을(`synthetic/`)과 혼용 금지**: ingest 시 `complex_id` 를 명확히 구분. 한빛마을은 `01HXXSOL0000000000000000AA` 예약.
- **민감 정보 주입 금지**: 실존 단지·개인 정보를 이 폴더에 절대 추가하지 말 것. 합성만.
- **편집 시**: 조문 번호 재정렬 금지. v1.md 의 조문 번호가 v2/v3 diff 의 참조와 회의록의 "관련 조문" 과 맞물려 있음.
