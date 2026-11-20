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
  local target=$1 overlay=$2 port address
  local -a stack=(docker compose --env-file .env.example -p "neti-smoke-$target-$$" -f compose.yaml -f "$overlay")
  cleanup() {
    local status=$?
    if ((status > 0)); then
      "${stack[@]}" ps -a || true
      "${stack[@]}" logs --tail=30 || true
    fi
    "${stack[@]}" down --remove-orphans
    return "$status"
  }
  trap cleanup EXIT

  "${stack[@]}" up --build -d --wait --wait-timeout 90
  port=$("${stack[@]}" port frontend 8080)
  address="http://$port"
  curl --fail --silent --show-error "$address/" | grep -F '<title>NETI Next</title>'
  curl --fail --silent --show-error "$address/api/health" | grep -F '{"status":"ok"}'
  curl --fail --silent --show-error "$address/agent/health" | grep -F '{"status":"ok"}'
  [[ $(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$address/agent/v1/answers" \
    -H 'Content-Type: application/json' -d '{"question":"Где расписание?"}') == 503 ]]
  "${stack[@]}" exec -T agent python -m agent --help >/dev/null
  echo "Smoke $target: ok"
)

case "$mode" in
  dev) probe dev compose.dev.yaml ;;
  prod) probe prod compose.build.yaml ;;
  both) probe dev compose.dev.yaml; probe prod compose.build.yaml ;;
  *) echo 'Использование: deploy/smoke.sh {dev|prod|both}' >&2; exit 2 ;;
esac
