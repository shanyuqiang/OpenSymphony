# Symphony-CC 아키텍처 결정 기록

## ADR-001: GitHub Issues 사용 (Linear 대신)
- **결정**: `gh` CLI로 GitHub Issues 폴링
- **근거**: gh CLI 이미 설치/인증됨, MCP 추가 불필요
- **상태**: 확정

## ADR-002: Python 3.12 기반
- **결정**: 핵심 로직은 Python, CLI wrapper만 bash
- **근거**: subprocess 관리, asyncio 동시성, JSON 조작에 적합
- **상태**: 확정

## ADR-003: Claude CLI --print 모드
- **결정**: `claude --print` + `--output-format stream-json`
- **근거**: 비대화형 자동화에 최적, 실시간 모니터링 가능
- **상태**: 확정

## ADR-004: 상태 머신 기반 오케스트레이션
- **결정**: JSON 기반 FSM (QUEUED→PREPARING→RUNNING→SUCCEEDED/FAILED)
- **근거**: 복구 가능한 상태 관리, 재시도 로직 단순화
- **상태**: 확정

## ADR-005: max_concurrent=2
- **결정**: 동시 실행 최대 2개
- **근거**: Max Plan rate limit 고려, 파이프라인 효과 활용
- **상태**: 확정
