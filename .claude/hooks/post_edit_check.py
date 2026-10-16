"""PostToolUse (Edit|Write): run the same checks CI runs on the edited file.

*.py             -> ruff check <file> (ruff.toml: syntax errors, undefined names)
frontend *.ts(x) -> tsc --noEmit for the frontend project
Exit 2 feeds the failure back to Claude; exit 0 is silent.
"""
import json
import os
import subprocess
import sys

# .claude/hooks/<this file> -> project root (CLAUDE_PROJECT_DIR may be a /c/... Git Bash path)
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
payload = json.load(sys.stdin)
path = payload.get("tool_input", {}).get("file_path") or payload.get("tool_response", {}).get("filePath", "")
if not path:
    sys.exit(0)
rel = os.path.relpath(os.path.abspath(path), root).replace("\\", "/")


def run(cmd: list[str], cwd: str) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=110)
    if proc.returncode != 0:
        out = (proc.stdout + proc.stderr).strip()
        print(f"{os.path.basename(cmd[0])} failed after editing {rel}:\n{out[-3000:]}", file=sys.stderr)
        sys.exit(2)


if rel.endswith(".py") and not rel.startswith((".venv/", "frontend/")):
    ruff = os.path.join(root, ".venv", "Scripts", "ruff.exe")
    if not os.path.exists(ruff):
        ruff = os.path.join(root, ".venv", "bin", "ruff")
    run([ruff, "check", "--quiet", rel], root)
elif rel.startswith("frontend/") and rel.endswith((".ts", ".tsx")) and "/node_modules/" not in rel:
    frontend = os.path.join(root, "frontend")
    tsc = os.path.join(frontend, "node_modules", ".bin", "tsc.cmd" if os.name == "nt" else "tsc")
    if os.path.exists(tsc):
        run([tsc, "--noEmit", "-p", "."], frontend)
