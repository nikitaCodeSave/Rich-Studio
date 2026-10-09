"""Claude Code hook (PostToolUse on Edit/Write): format the file the agent just changed.

Never fails the tool call: missing tools or lint errors are left for `make check`.
"""

import contextlib
import json
import subprocess
import sys

try:
    path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
except ValueError:
    sys.exit(0)

if path.endswith(".py"):
    cmds = [["ruff", "format", "-q", path], ["ruff", "check", "-q", "--fix", path]]
elif path.endswith((".js", ".mjs")) and "/vendor/" not in path:
    cmds = [["npx", "--no-install", "eslint", "--fix", path]]
else:
    cmds = []

for cmd in cmds:
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)  # noqa: S603
