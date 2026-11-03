#!/usr/bin/env bash
# Browser checks on a stand of their own: the fake Telegram and a studio with an empty DATA_DIR on ports of their own,
# so a working studio on 7424 keeps running and never sees the test posts. Both stop on exit; on a failure the
# stand's logs stay in the printed folder. Usage: make e2e-local [ONLY=<part of a check file name>]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"   # screenshots go to test-results/ui/ here
PY=$(if [ -x "$ROOT/.venv/bin/python" ]; then echo "$ROOT/.venv/bin/python"; else command -v python3; fi)
STUDIO_PORT=${STUDIO_PORT:-7499}
MOCK_PORT=${MOCK_PORT:-8099}

for port in "$STUDIO_PORT" "$MOCK_PORT"; do
  if ! "$PY" -c "import socket,sys; socket.socket().bind(('127.0.0.1', int(sys.argv[1])))" "$port" 2>/dev/null; then
    echo "port $port is busy: free it or set STUDIO_PORT / MOCK_PORT" >&2
    exit 2
  fi
done

WORK=$(mktemp -d /tmp/rs-e2e-XXXX)
pids=()
status=1
stop() {
  if [ ${#pids[@]} -gt 0 ]; then kill "${pids[@]}" 2>/dev/null || true; wait "${pids[@]}" 2>/dev/null || true; fi
  if [ "$status" -eq 0 ]; then rm -rf "$WORK"; else echo "stand logs: $WORK/mock.log, $WORK/studio.log" >&2; fi
}
trap stop EXIT

(cd "$ROOT/tests" && exec "$PY" -m uvicorn mock_telegram:app --host 127.0.0.1 --port "$MOCK_PORT") >"$WORK/mock.log" 2>&1 &
pids+=($!)
mkdir -p "$WORK/data"
(cd "$ROOT/app" && DATA_DIR="$WORK/data" TG_BOT_TOKEN=1:x TG_API_BASE="http://127.0.0.1:$MOCK_PORT" \
  TG_DEFAULT_CHAT=@test TG_PREVIEW_CHAT=777 SPEC_URL=http://127.0.0.1:9/off \
  exec "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$STUDIO_PORT") >"$WORK/studio.log" 2>&1 &
pids+=($!)

for url in "http://127.0.0.1:$MOCK_PORT/log" "http://127.0.0.1:$STUDIO_PORT/api/config"; do
  curl -sf --retry 30 --retry-connrefused --retry-delay 1 -o /dev/null "$url" || { echo "stand did not start: $url" >&2; exit 1; }
done

set +e
BASE="http://localhost:$STUDIO_PORT" MOCK="http://localhost:$MOCK_PORT" E2E_ONLY="${1:-}" node "$ROOT/tests/e2e_ui.mjs"
status=$?
exit "$status"
