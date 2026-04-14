# apt-domain-mcp

공동주택 도메인 지식(관리규약·입대의 회의록·운영 산출물)을 단지 단위로 제공하는 MCP 서버.

상위 파이프라인:
```
ChatGPT Enterprise CustomGPT
       ↓ (A2A)
Vertical AI Agent (apt-legal-agent)
       ↓ (MCP)
   ┌───┴────────────────────────┐
   ↓                            ↓
kor-legal-mcp              apt-domain-mcp  ← 본 리포
(법령·판례·자치법규)         (단지별 규약·회의록·위키)
   ↓                            ↓
law.go.kr Open API          PostgreSQL (+ 선택적 Milvus)
```

본 리포는 **단지별 도메인 지식**만 다룬다. 법령·판례·자치법규 일반 조회는 `kor-legal-mcp`의 책임이다.

## 현재 상태
Phase 0: 스캐폴드 + 가상 단지("한빛마을 새솔아파트") 합성 데이터 + PostgreSQL 스키마 설계 단계.
실제 단지 데이터 인제스트는 Phase 1에서 진행 예정.

## 문서
- `docs/01_architecture.md` — 3계층 저장소 모델, 인제스트 플로, MCP tool 명세
- `docs/02_synthetic_complex_spec.md` — 가상 단지 스펙
- `sql/schema.sql` — PostgreSQL 스키마
- `synthetic/` — 가상 단지 원본 문서 (마크다운 + 생성된 PDF)
