#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
root=$(cd .. && pwd)
export APP_PORT=0 APP_BIND=127.0.0.1
export FRONTEND_ENV_FILE="$root/frontend/.env.example"
export BACKEND_ENV_FILE="$root/backend/.env.example"
export AGENT_ENV_FILE="$root/agent/.env.example"
export INFERENCE_ENV_FILE="$root/inference/.env.example"
fixtures=$(mktemp -d)
trap 'rm -rf -- "$fixtures"' EXIT
source ./cache-env.sh
init_cache_env "$fixtures/cache"
export AGENT_PROMPTS_FILE="$fixtures/unused.env"
export INFERENCE_PROMPTS_FILE="$fixtures/unused.env"
export AGENT_PROMPTS_DIR="$fixtures/agent"
export INFERENCE_PROMPTS_DIR="$fixtures/inference"
install -d -m 0755 "$AGENT_PROMPTS_DIR" "$INFERENCE_PROMPTS_DIR"
echo 'Проверочный промпт агента.' >"$AGENT_PROMPTS_DIR/text-system-prompt.txt"
echo 'Проверочный промпт OCR.' >"$INFERENCE_PROMPTS_DIR/ocr-prompt.txt"
chmod 0644 "$AGENT_PROMPTS_DIR/text-system-prompt.txt" "$INFERENCE_PROMPTS_DIR/ocr-prompt.txt"
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
  for route in / /chat /login /register; do
    curl --fail --silent --show-error "$address$route" | grep -F '<title>Пигеон</title>'
  done
  curl --fail --silent --show-error "$address/api/health" | grep -F '{"status":"ok"}'
  curl --silent --show-error "$address/api/v1/chats" | python3 -c '
import json, sys
problem = json.load(sys.stdin)
assert problem["type"] == "urn:neti:problem:unauthorized"
assert problem["status"] == 401
assert len(problem["request_id"]) == 32
'
  local guest headers
  guest=$(curl --fail --silent --show-error -X POST "$address/api/v1/auth/guest" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])')
  headers=$(curl --silent --http1.1 --max-time 2 -D - -o /dev/null "$address/api/v1/events" \
    -H "Origin: $address" -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
    -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    -H "Sec-WebSocket-Protocol: neti.v1, guest.$guest" || true)
  grep -F '101 Switching Protocols' <<<"$headers"
  curl --fail --silent --show-error -X DELETE "$address/api/v1/auth/guest" -H "Authorization: Bearer $guest"
  [[ $(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$address/agent/v1/answers" \
    -H 'Content-Type: application/json' -d '{"question":"Где расписание?"}') == 404 ]]
  "${stack[@]}" exec -T backend wget -q -O - http://agent:8001/agent/health | grep -F '{"status":"ok"}'
  "${stack[@]}" exec -T backend wget -q -O - http://inference:8002/inference/health | grep -F '{"status":"ok"}'
  "${stack[@]}" exec -T backend sh -c 'command -v pdfinfo && command -v pdftotext && command -v pdfseparate && tesseract --list-langs | grep -qx rus'
  "${stack[@]}" exec -T agent python -c '
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

base = "http://inference:8002/inference"
with urlopen(base + "/v1/capabilities", timeout=5) as response:
    capabilities = json.load(response)
assert set(capabilities["models"]) == {"llm", "asr", "ocr", "decision"}
assert capabilities["configured"] is False
request = Request(
    base + "/v1/generate",
    data=json.dumps({"max_tokens": 16, "messages": [{"role": "user", "content": "test"}]}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    urlopen(request, timeout=5)
except HTTPError as error:
    assert error.code == 503, error.code
    assert len(error.headers["X-Request-ID"]) == 32
    assert "detail" in json.load(error)
else:
    raise AssertionError("Expected an unconfigured provider error")
decision = Request(
    base + "/v1/decide",
    data=json.dumps({
        "state": "test", "instructions": "select a route",
        "choices": {"search": "lookup", "reply": "direct reply"},
    }).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    urlopen(decision, timeout=5)
except HTTPError as error:
    assert error.code == 503, error.code
else:
    raise AssertionError("Expected an unconfigured decision provider error")
'
  image=$("${stack[@]}" images -q agent)
  docker run --rm --network "$AGENT_DOCKER_NETWORK" --entrypoint python "$image" -c '
import json
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
    assert error.headers.get_content_type() == "application/problem+json"
    assert json.load(error)["request_id"] == error.headers["X-Request-ID"]
else:
    raise AssertionError("Expected disabled provider response")
'
  "${stack[@]}" exec -T agent neti-agent --help >/dev/null
  "${stack[@]}" exec -T agent python -c '
import os
from uuid import uuid4

from agent.providers.document_cache import _get_cache, cached_document, close_document_cache

dsn = os.environ["AGENT_CACHE_DSN"]
calls = []
prefix = uuid4().hex
first = f"https://example.org/{prefix}/first"
second = f"https://example.org/{prefix}/second"

def fetch(url):
    calls.append(url)
    return f"Публичный документ {url}"

def read(url, callback=fetch):
    return cached_document(url, callback, dsn=dsn, ttl=60, capacity=1)

try:
    assert read(first) == read(first)
    assert calls == [first]
    close_document_cache()
    assert read(first, None)
    cache = _get_cache(dsn)
    with cache.pool.connection() as connection:
        connection.execute("UPDATE document_cache.documents SET verified_at = 0")
        row = connection.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
        assert row == (False,)
    assert read(first, None) == ""
    assert read(first)
    assert read(second)
    assert read(first, None) == ""
    assert read(second, None)

    from concurrent.futures import ThreadPoolExecutor
    concurrent = f"https://example.org/{prefix}/concurrent"
    with ThreadPoolExecutor(max_workers=4) as pool:
        copies = list(pool.map(read, [concurrent] * 4))
    assert all(copies) and len(set(copies)) == 1
    assert calls.count(concurrent) == 1
finally:
    close_document_cache()
'
  "${stack[@]}" exec -T agent python -c '
from agent.application.skills import select_skills
from agent.config import PROMPTS_DIRECTORY, SKILLS_DIRECTORY, load_settings

settings = load_settings()
assert settings["TEXT_SYSTEM_PROMPT"] == "Проверочный промпт агента."
assert settings["WEB_SYSTEM_PROMPT"]
active = select_skills(
    "Сравни сроки подачи в НГУ и НГТУ",
    root=SKILLS_DIRECTORY,
    overrides=PROMPTS_DIRECTORY / "skills",
    legacy=lambda _: None,
    attachment=False,
    photo_only=False,
    table=False,
)
assert active.ids == ("comparison", "calendar")
assert active.events and "table" in active.blocks
'
  "${stack[@]}" exec -T inference python -c '
from inference.config import Settings

settings = Settings().routerai
assert settings.ocr_prompt == "Проверочный промпт OCR."
assert settings.pdf_ocr_prompt
'
  "${stack[@]}" exec -T postgres sh -eu <<'SH'
query() {
  PGPASSWORD="$1" psql -h 127.0.0.1 -U "$2" -d neti -X -q -v ON_ERROR_STOP=1 -v VERBOSITY=verbose -c "$3"
}

denied() {
  if result=$(query "$@" 2>&1); then
    echo "Unexpected permission: $2: $3" >&2
    exit 1
  fi
  echo "$result" | grep -q '42501'
}

query "$AGENT_CACHE_PASSWORD" agent_cache 'SELECT 1 FROM document_cache.documents LIMIT 1'
query "$BACKEND_DATABASE_PASSWORD" neti_backend 'SELECT 1 FROM backend.workspaces LIMIT 1'
denied "$AGENT_CACHE_PASSWORD" agent_cache 'SELECT state FROM backend.workspaces'
denied "$AGENT_CACHE_PASSWORD" agent_cache 'DELETE FROM backend.workspaces'
denied "$AGENT_CACHE_PASSWORD" agent_cache 'SET ROLE neti_backend'
denied "$BACKEND_DATABASE_PASSWORD" neti_backend 'SELECT markdown FROM document_cache.documents'
denied "$BACKEND_DATABASE_PASSWORD" neti_backend 'DELETE FROM document_cache.documents'
denied "$BACKEND_DATABASE_PASSWORD" neti_backend 'SET ROLE agent_cache'
denied "$BACKEND_DATABASE_PASSWORD" neti_backend 'CREATE TABLE public.boundary_probe (id int)'
SH
  if [[ $target == dev ]]; then
    local go_image
    go_image=$(docker build -q --target build ../backend)
    docker run --rm --network "neti-smoke-$target-$$"_database       --env-file "$BACKEND_DATABASE_ENV_FILE"       -e 'BACKEND_TEST_DATABASE_DSN=host=postgres dbname=neti user=neti_backend sslmode=disable connect_timeout=5'       "$go_image" go test -count=1 -v ./...
  fi
  echo "Smoke $target: ok"
)

case "$mode" in
  dev) probe dev compose.dev.yaml ;;
  prod) probe prod compose.build.yaml ;;
  both) probe dev compose.dev.yaml; probe prod compose.build.yaml ;;
  *) echo 'Использование: deploy/smoke.sh {dev|prod|both}' >&2; exit 2 ;;
esac
