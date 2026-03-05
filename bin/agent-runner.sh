#!/usr/bin/env bash
# agent-runner.sh - worktree 디렉토리에서 claude --print를 실행하는 wrapper
#
# 비유: 공장의 생산 라인 관리자. 작업 지시서(프롬프트)를 받아 기계(claude)를 가동하고,
# 동시에 여러 라인이 충돌하지 않도록 잠금(lock)을 관리하며,
# 비정상 종료 시 자동으로 청소한다.
#
# 사용법: agent-runner.sh <issue-number> <worktree-path> <prompt> [options]
#   옵션:
#     --model <model>          모델 (기본: opus)
#     --max-budget <usd>       최대 예산 (기본: 5)
#     --allowed-tools <tools>  허용 도구 목록
#     --timeout <seconds>      타임아웃 (기본: 600)

set -euo pipefail

# --- 설정 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
STATE_DIR="${PROJECT_ROOT}/state/active"
LOGS_DIR="${PROJECT_ROOT}/logs"

# --- 인자 파싱 ---
if [[ $# -lt 3 ]]; then
    echo "사용법: $0 <issue-number> <worktree-path> <prompt> [options]" >&2
    exit 1
fi

ISSUE_NUMBER="$1"
WORKTREE_PATH="$2"
PROMPT="$3"
shift 3

# 기본값
MODEL="opus"
MAX_BUDGET="5"
ALLOWED_TOOLS="Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)"
TIMEOUT=600

# 옵션 파싱
while [[ $# -gt 0 ]]; do
    case "$1" in
        --model)
            MODEL="$2"; shift 2 ;;
        --max-budget)
            MAX_BUDGET="$2"; shift 2 ;;
        --allowed-tools)
            ALLOWED_TOOLS="$2"; shift 2 ;;
        --timeout)
            TIMEOUT="$2"; shift 2 ;;
        *)
            echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
    esac
done

# --- 디렉토리 준비 ---
ISSUE_LOG_DIR="${LOGS_DIR}/issue-${ISSUE_NUMBER}"
mkdir -p "$STATE_DIR" "$ISSUE_LOG_DIR"

PID_FILE="${STATE_DIR}/issue-${ISSUE_NUMBER}.pid"
LOCK_DIR="${STATE_DIR}/issue-${ISSUE_NUMBER}.lock"
LOG_FILE="${ISSUE_LOG_DIR}/agent.log"

# --- 동시 실행 제어 (mkdir 기반 atomic lock) ---
# macOS에는 flock이 없으므로 mkdir의 원자성을 활용
acquire_lock() {
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
        # 잠금 디렉토리가 존재 — stale 여부 확인
        local existing_pid_file="${LOCK_DIR}/pid"
        if [[ -f "$existing_pid_file" ]]; then
            local existing_pid
            existing_pid=$(cat "$existing_pid_file")
            if kill -0 "$existing_pid" 2>/dev/null; then
                echo "이슈 #${ISSUE_NUMBER}는 이미 실행 중입니다 (PID: ${existing_pid})" >&2
                exit 1
            fi
            # stale lock 제거
            echo "stale lock 제거: PID ${existing_pid}는 더 이상 실행 중이 아님" | tee -a "$LOG_FILE"
            rm -rf "$LOCK_DIR"
            mkdir "$LOCK_DIR"
        else
            # PID 파일 없는 비정상 lock — 제거
            rm -rf "$LOCK_DIR"
            mkdir "$LOCK_DIR"
        fi
    fi
    echo $$ > "${LOCK_DIR}/pid"
}

release_lock() {
    rm -rf "$LOCK_DIR"
}

# --- PID 파일 관리 ---
write_pid() {
    echo $$ > "$PID_FILE"
}

remove_pid() {
    rm -f "$PID_FILE"
}

# --- cleanup 트랩 ---
cleanup() {
    local exit_code=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 정리 중 (exit code: ${exit_code})" >> "$LOG_FILE"
    remove_pid
    release_lock
    exit "$exit_code"
}

trap cleanup EXIT INT TERM

# --- 메인 실행 ---
acquire_lock
write_pid

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 에이전트 시작: issue #${ISSUE_NUMBER}" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] worktree: ${WORKTREE_PATH}" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] model: ${MODEL}, budget: \$${MAX_BUDGET}" >> "$LOG_FILE"

# worktree 존재 확인
if [[ ! -d "$WORKTREE_PATH" ]]; then
    echo "worktree 경로가 존재하지 않습니다: ${WORKTREE_PATH}" >&2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 에러: worktree 경로 없음" >> "$LOG_FILE"
    exit 1
fi

# claude CLI 실행
cd "$WORKTREE_PATH"

claude --print \
    --model "$MODEL" \
    --max-budget-usd "$MAX_BUDGET" \
    --output-format stream-json \
    --allowedTools "$ALLOWED_TOOLS" \
    -p "$PROMPT" \
    2>> "$LOG_FILE" \
    >> "${ISSUE_LOG_DIR}/output.jsonl"

EXIT_CODE=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 에이전트 종료: exit code ${EXIT_CODE}" >> "$LOG_FILE"

exit "${EXIT_CODE}"
