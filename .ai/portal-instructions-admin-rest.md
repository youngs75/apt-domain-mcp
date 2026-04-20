# apt-legal-agent/docs/01_architecture.md 업데이트 메모

> 다음 apt-legal-agent 세션에서 반영할 것.

## 수정 포인트

1. "3-repo 구조" -> "4-repo 구조 (+ apt-web)" 표현으로 갱신 (apt-web 누락돼 있다면)
2. 데이터 흐름 다이어그램에 `apt-web -> apt-domain-mcp /admin/api/*` 경로 추가
3. "DB 분리 원칙" 섹션에 본 세션 커밋 SHA 인용
4. 인증 방식을 "API Key 헤더 단일 (교육과제 범위)"로 명시

## 변경 배경

- 날짜: 2026-04-20
- apt-web의 asyncpg 직접 접근 제거 -> apt-domain-mcp `/admin/api/*` REST 경유로 통일
- 인증은 API Key 단일 (교육과제 범위)
- 관련 리포 커밋 SHA: apt-domain-mcp 측은 본 세션 커밋 참조, apt-web 측은 후속 세션에서 반영
