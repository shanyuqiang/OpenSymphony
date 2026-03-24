#!/bin/bash
# Symphony-CC 폴링 루프
# gh issue list로 trigger_label이 붙은 이슈를 조회한다.
#
# 사용법: symphony-poller.sh <repo> <label> [interval_s]
# 예: symphony-poller.sh qjc-office/qjc-webapp "symphony:ready" 30

set -euo pipefail

REPO="${1:?사용법: $0 <repo> <label> [interval_s]}"
LABEL="${2:?라벨을 지정하세요}"
INTERVAL="${3:-30}"

echo "[poller] 시작: repo=$REPO, label=$LABEL, interval=${INTERVAL}s"

while true; do
    ISSUES=$(gh issue list \
        --repo "$REPO" \
        --label "$LABEL" \
        --json "number,title" \
        --limit 100 \
        2>/dev/null || echo "[]")

    COUNT=$(echo "$ISSUES" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [ "$COUNT" -gt 0 ]; then
        echo "[poller] 발견: ${COUNT}개 이슈"
        echo "$ISSUES" | python3 -c "
import sys, json
for issue in json.load(sys.stdin):
    print(f\"  #{issue['number']} - {issue['title']}\")
"
    fi

    sleep "$INTERVAL"
done
