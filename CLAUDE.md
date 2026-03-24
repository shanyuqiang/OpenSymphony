# Symphony-CC

Claude Code 특화 자율 오케스트레이션 서비스.
GitHub 이슈를 감시하고, Claude Code 에이전트를 자동으로 실행하여 작업을 처리한다.

## 프로젝트 구조

```
symphony-cc/
├── bin/                    # CLI 및 셸 스크립트
│   ├── symphonyctl         # 메인 CLI 진입점
│   ├── symphony-poller.sh  # 이슈 폴링 데몬
│   └── agent-runner.sh     # 에이전트 실행 래퍼
├── lib/                    # Python 핵심 모듈
│   ├── cli.py              # CLI 명령어 정의
│   ├── config.py           # config.yaml 파싱 및 검증
│   ├── tracker.py          # GitHub 이슈 추적기
│   ├── orchestrator.py     # 작업 스케줄링 및 오케스트레이션
│   ├── runner.py           # Claude Code 에이전트 실행기
│   ├── workspace.py        # 작업 디렉토리 관리
│   ├── workflow.py         # 워크플로우 상태 머신
│   ├── notifier.py         # GitHub 코멘트 / Slack 알림
│   ├── dashboard.py        # 상태 대시보드
│   ├── logger.py           # 구조화 JSON 로깅
│   └── init.py             # 프로젝트 초기화
├── templates/              # 워크플로우 및 이슈 템플릿
├── tests/                  # 테스트
├── state/                  # 런타임 상태 (git 제외)
├── logs/                   # 로그 (git 제외)
├── config.yaml.example     # 설정 예시
└── pyproject.toml          # 패키지 설정
```

## 개발 환경 셋업

```bash
# 1. 저장소 클론
git clone https://github.com/your-org/symphony-cc.git
cd symphony-cc

# 2. 가상환경 생성 및 활성화
python3.12 -m venv .venv
source .venv/bin/activate

# 3. 개발 의존성 포함 설치
pip install -e ".[dev]"

# 4. 설정 파일 복사 후 편집
cp config.yaml.example config.yaml
# config.yaml에서 repo, webhook 등 설정
```

## 테스트 실행

```bash
# 전체 테스트
pytest

# 특정 모듈 테스트
pytest tests/test_runner.py

# 상세 출력
pytest -v
```

## 컨벤션

- Python 3.12+, asyncio 기반
- 타입 힌트 필수
- 테스트: pytest + pytest-asyncio
- 로깅: 구조화 JSON 로그

## 기여

[CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.
