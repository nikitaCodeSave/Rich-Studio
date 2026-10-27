#!/bin/bash
# SessionStart in a Claude Code cloud session: the tools `make check` needs, so an agent starts with a working check.
# Python tools go to .venv (the system Python may be managed by the OS), JS tools to node_modules.
# Idempotent and quiet: a second start only checks stamps. Local sessions are left alone.
# A failed install (no network) is a warning, not a failed session: `make check` then says what is missing.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR" || exit 0

warn() { echo "session-start: $1; make check может не запуститься" >&2; }

[ -x .venv/bin/python ] || python3 -m venv .venv || warn "не создан .venv"
# reinstall only when the pinned versions changed since the last install
stamp=.venv/.requirements.sha
want=$(cat requirements.txt requirements-dev.txt | sha256sum | cut -d' ' -f1)
if [ -x .venv/bin/pip ] && [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  if .venv/bin/pip install -q --disable-pip-version-check -r requirements-dev.txt >&2; then
    echo "$want" > "$stamp"
  else
    warn "pip install не прошёл"
  fi
fi

# npm ci, not npm install: never rewrite package-lock.json in an agent's working tree
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules ]; then
  if npm ci --no-audit --no-fund --silent >&2; then
    touch node_modules
  else
    warn "npm ci не прошёл"
  fi
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  add() { grep -qxF "$1" "$CLAUDE_ENV_FILE" 2>/dev/null || echo "$1" >> "$CLAUDE_ENV_FILE"; }
  add "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:$CLAUDE_PROJECT_DIR/node_modules/.bin:\$PATH\""
  # the cloud image has Chromium for Playwright; the npm Playwright would look for its own build
  if [ -x /opt/pw-browsers/chromium ]; then
    add "export CHROMIUM=/opt/pw-browsers/chromium"
  fi
fi
exit 0
