#!/usr/bin/env bash
# The built image, locked down as on the server (read-only root, no capabilities, uid 10001, /data a named volume)
# and with no network at all: it must start and answer while the spec and Telegram are out of reach (the bot token
# is fake), stop on SIGTERM with exit code 0 within stop_grace_period of deploy/docker-compose.yml, and keep a draft
# made through the API across docker stop / docker start. CI runs it (ci.yml, job docker).
# Locally: docker build -t rich-studio . && scripts/image_smoke.sh
set -euo pipefail
IMAGE=${IMAGE:-rich-studio}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAME=rs-smoke-$$ # the container and its volume; a working studio next to it keeps running
GRACE=$(sed -n 's/^ *stop_grace_period: *\([0-9][0-9]*\)s.*/\1/p' "$ROOT/deploy/docker-compose.yml" | head -n 1)
if [ -z "$GRACE" ]; then
  echo "FAIL: no stop_grace_period: <N>s in deploy/docker-compose.yml" >&2
  exit 2
fi

status=1
cleanup() {
  if [ "$status" -ne 0 ] && docker inspect "$NAME" >/dev/null 2>&1; then
    echo "--- docker logs $NAME, last 60 lines" >&2
    docker logs --tail 60 "$NAME" >&2 || true
  fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# METHOD PATH [JSON] → the answer's body; an HTTP error fails. No port is published, and the image has no curl
call() {
  docker exec "$NAME" python -c '
import sys, urllib.error, urllib.request
method, path, body = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else None)
req = urllib.request.Request("http://127.0.0.1:8080" + path, method=method, data=body and body.encode(),
    headers={"Content-Type": "application/json"})
try:
    print(urllib.request.urlopen(req, timeout=10).read().decode())
except urllib.error.HTTPError as e:
    sys.exit(f"{method} {path}: HTTP {e.code} {e.read().decode()[:300]}")' "$@"
}
wait_ready() {
  local deadline=$((SECONDS + 60))
  until call GET /healthz >/dev/null 2>&1; do
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || fail "the container exited while starting"
    [ "$SECONDS" -lt "$deadline" ] || fail "no /healthz within 60 s"
    sleep 0.5
  done
}
ms() { echo $(($(date +%s%N) / 1000000)); }
stop_gracefully() {
  local t0 took code
  t0=$(ms)
  docker stop -t "$GRACE" "$NAME" >/dev/null
  took=$(($(ms) - t0))
  code=$(docker inspect -f '{{.State.ExitCode}}' "$NAME")
  # past the grace period Docker kills the process: 137
  [ "$code" = 0 ] || fail "docker stop: exit code $code, not 0 (stop_grace_period ${GRACE}s)"
  [[ "$(docker logs "$NAME" 2>&1)" == *"studio stopped"* ]] || fail "no 'studio stopped' in the log: the shutdown did not finish"
  echo "ok: docker stop took ${took} ms, exit code 0 (stop_grace_period ${GRACE}s)"
}

echo "== start with --network none"
docker run -d --name "$NAME" --network none -v "$NAME:/data" --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges -e TG_BOT_TOKEN=1:image-smoke "$IMAGE" >/dev/null
wait_ready
call GET /api/config >/dev/null
call GET / >/dev/null
[ "$(docker exec "$NAME" id -u)" = 10001 ] || fail "the studio does not run as uid 10001"
# 0.0.0.0:8080 in LISTEN: Caddy and a published port reach it from outside the container
docker exec "$NAME" grep -q ' 00000000:1F90 00000000:0000 0A ' /proc/net/tcp || fail "nothing listens on 0.0.0.0:8080"
echo "ok: /healthz, /api/config and / answer; uid 10001; listens on 0.0.0.0:8080"

echo "== a draft to find after the restart"
POST='{"rich_message": {"markdown": "Черновик **переживает** перезапуск"}}'
id=$(call POST /api/drafts "{\"title\": \"image-smoke\", \"post\": $POST}" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])') || fail "POST /api/drafts"

echo "== docker stop, docker start"
stop_gracefully
docker start "$NAME" >/dev/null
wait_ready
draft=$(call GET "/api/drafts/$id") || fail "draft $id is gone after the restart"
python3 -c '
import json, sys
d, post = json.loads(sys.argv[1]), json.loads(sys.argv[2])
assert d["title"] == "image-smoke" and d["payload"] == post, d' "$draft" "$POST" || fail "draft $id changed after the restart"
echo "ok: draft $id is the same after the restart"
status=0
echo "OK: the image starts without network, stops gracefully and keeps its data"
