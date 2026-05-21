#!/usr/bin/env bash
# CodePal post-commit hook — auto-index changed files
# Managed by: codepal hooks install
# DO NOT EDIT THIS LINE: codepal-managed

set -euo pipefail

CODEPAL_URL="${CODEPAL_URL:-http://127.0.0.1:8742}"
PROJECT_PATH="$(git rev-parse --show-toplevel)"

# Get files changed in the last commit
CHANGED_FILES=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || true)

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

# Build JSON array of absolute paths
FILES_JSON=$(echo "$CHANGED_FILES" | python3 -c "
import sys, json, os
files = [line.strip() for line in sys.stdin if line.strip()]
project = os.environ.get('PROJECT_PATH', os.getcwd())
full_paths = [os.path.join(project, f) for f in files]
print(json.dumps({'files': full_paths}))
")

# Call CodePal index endpoint (fail silently if service is down)
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H 'Content-Type: application/json' \
    -d "$FILES_JSON" \
    --max-time 10 \
    "$CODEPAL_URL/v1/index" 2>/dev/null || echo "000")

if [ "$HTTP_STATUS" = "000" ] || [ "$HTTP_STATUS" = "" ]; then
    echo "[codepal] Warning: CodePal service not running — skipping auto-index" >&2
fi

exit 0
