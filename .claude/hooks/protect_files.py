"""PreToolUse (Edit|Write): refuse edits to committed migrations, lockfiles and .env.

A migration counts as applied once it is committed: CI and Render apply every
committed file, and the API refuses a schema that differs. Uncommitted
migrations (still being written) and brand-new files stay editable.
Exit 2 blocks the tool call and shows stderr to Claude.
"""
import json
import os
import subprocess
import sys

# .claude/hooks/<this file> -> project root (CLAUDE_PROJECT_DIR may be a /c/... Git Bash path)
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
rel = os.path.relpath(os.path.abspath(path), root).replace("\\", "/") if path else ""
name = rel.rsplit("/", 1)[-1]


def block(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(2)


if rel.startswith("db/migrations/") and rel.endswith(".sql") and os.path.exists(path):
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel], cwd=root, capture_output=True
    ).returncode == 0
    if tracked:
        block(f"Blocked: {rel} is committed, so it is applied in CI/production. "
              "Never edit an applied migration; add the next NNN_*.sql instead.")

if name in ("package-lock.json", "uv.lock", "poetry.lock"):
    block(f"Blocked: {rel} is a lockfile. Change dependencies with npm/pip so it regenerates.")

if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
    block(f"Blocked: {rel} holds real secrets. Edit .env.example and ask the user to update {name}.")
