"""Git hook installer."""
from __future__ import annotations

import stat
from pathlib import Path

HOOK_TEMPLATE = """\
#!/bin/sh
# CodePal post-commit hook
# Auto-indexes changed files after each commit.

CODEPAL_URL="${CODEPAL_URL:-http://127.0.0.1:8742}"

# Get list of changed files in this commit
CHANGED=$(git diff --name-only HEAD~1 HEAD 2>/dev/null || git diff --name-only HEAD 2>/dev/null)

if [ -z "$CHANGED" ]; then
    exit 0
fi

# Build JSON array of absolute paths
PROJECT_PATH="$(git rev-parse --show-toplevel)"
FILES_JSON=$(echo "$CHANGED" | awk -v root="$PROJECT_PATH" 'BEGIN{printf "["} {printf "%s\\"%s/%s\\"", (NR>1?",":""), root, $0} END{print "]"}')

# Call CodePal index endpoint; fail silently if service not running
curl -sf -X POST "$CODEPAL_URL/v1/index" \\
    -H "Content-Type: application/json" \\
    -d "{\\"files\\": $FILES_JSON}" \\
    > /dev/null 2>&1 || true

exit 0
"""


def install_hook(project_path: str) -> None:
    """Install the post-commit hook into the given project's .git/hooks/."""
    hooks_dir = Path(project_path) / ".git" / "hooks"
    if not hooks_dir.exists():
        raise FileNotFoundError(f"No .git/hooks directory found at {project_path}")

    hook_path = hooks_dir / "post-commit"

    # Idempotent: if the hook already contains our marker, skip
    if hook_path.exists():
        existing = hook_path.read_text()
        if "CodePal post-commit hook" in existing:
            return

    hook_path.write_text(HOOK_TEMPLATE)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
