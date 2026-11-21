#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
root=$(cd .. && pwd)
export APP_PORT=0 APP_BIND=127.0.0.1
export FRONTEND_ENV_FILE="$root/frontend/.env.example"
export BACKEND_ENV_FILE="$root/backend/.env.example"
export AGENT_ENV_FILE="$root/agent/.env.example"
export IMAGE_PREFIX=neti-next-smoke IMAGE_TAG=local
mode=${1:-both}

probe() (
  local target=$1 overlay=$2 port address image
  export AGENT_DOCKER_NETWORK="neti-smoke-$target-$$-consumers"
  local -a stack=(docker compose --env-file .env.example -p "neti-smoke-$target-$$" -f compose.yaml -f "$overlay" -f compose.agent-network.yaml)
  docker network create "$AGENT_DOCKER_NETWORK" >/dev/null
  cleanup() {
    local status=$?
    if ((status > 0)); then
      "${stack[@]}" ps -a || true
      "${stack[@]}" logs --tail=30 || true
    fi
    "${stack[@]}" down --remove-orphans --volumes
    docker network rm "$AGENT_DOCKER_NETWORK" >/dev/null
    return "$status"
  }
  trap cleanup EXIT

  "${stack[@]}" up --build -d --wait --wait-timeout 90
  port=$("${stack[@]}" port frontend 8080)
  address="http://$port"
  curl --fail --silent --show-error "$address/" | grep -F '<title>NETI Next</title>'
  curl --fail --silent --show-error "$address/api/health" | grep -F '{"status":"ok"}'
  local guest headers
  guest=$(curl --fail --silent --show-error -X POST "$address/api/v1/auth/guest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')
  headers=$(curl --silent --http1.1 --max-time 2 -D - -o /dev/null "$address/api/v1/events" \
    -H "Origin: $address" -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    -H "Sec-WebSocket-Protocol: neti.v1, guest.$guest" || true)
  grep -F '101 Switching Protocols' <<<"$headers"
  curl --fail --silent --show-error -X DELETE "$address/api/v1/auth/guest" -H "Authorization: Bearer $guest"
  curl --fail --silent --show-error "$address/agent/health" | grep -F '{"status":"ok"}'
  [[ $(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$address/agent/v1/answers" \
    -H 'Content-Type: application/json' -d '{"question":"Где расписание?"}') == 503 ]]
  "${stack[@]}" exec -T backend wget -q -O - http://agent:8001/agent/health | grep -F '{"status":"ok"}'
  image=$("${stack[@]}" images -q agent)
  docker run --rm --network "$AGENT_DOCKER_NETWORK" --entrypoint python "$image" -c '
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = "http://neti-agent:8001/agent"
assert urlopen(base + "/health", timeout=5).read() == b"{\"status\":\"ok\"}"
request = Request(
    base + "/v1/answers",
    data=b"{\"question\":\"test\"}",
    headers={"Content-Type": "application/json"},
)
try:
    urlopen(request, timeout=5)
except HTTPError as error:
    assert error.code == 503, error.code
else:
    raise AssertionError("Expected disabled provider response")
'
  "${stack[@]}" exec -T agent python -m agent --help >/dev/null
  echo "Smoke $target: ok"
)

case "$mode" in
  dev) probe dev compose.dev.yaml ;;
  prod) probe prod compose.build.yaml ;;
  both) probe dev compose.dev.yaml; probe prod compose.build.yaml ;;
  *) echo 'Использование: deploy/smoke.sh {dev|prod|both}' >&2; exit 2 ;;
esac
