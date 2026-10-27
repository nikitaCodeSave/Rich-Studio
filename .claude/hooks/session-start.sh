#!/bin/bash
# SessionStart in a Claude Code cloud session: the tools `make check` needs, so an agent starts with a working check.
# Python tools go to .venv (the system Python may be managed by the OS), JS tools to node_modules.
# Idempotent and quiet: a second start only refreshes PATH. Local sessions are left alone.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# reinstall only when the pinned versions changed since the last install
stamp=.venv/.requirements.sha
want=$(cat requirements.txt requirements-dev.txt | sha256sum | cut -d' ' -f1)
if [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  .venv/bin/pip install -q --disable-pip-version-check -r requirements-dev.txt >&2
  echo "$want" > "$stamp"
fi

if [ ! -d node_modules ] || [ package-lock.json -nt node_modules ]; then
  npm install --no-audit --no-fund --silent >&2
  touch node_modules
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:$CLAUDE_PROJECT_DIR/node_modules/.bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
  # the cloud image has Chromium for Playwright; the npm Playwright would look for its own build
  if [ -x /opt/pw-browsers/chromium ]; then
    echo "export CHROMIUM=/opt/pw-browsers/chromium" >> "$CLAUDE_ENV_FILE"
  fi
fi
