#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
root=$(cd .. && pwd)

init_env() {
  local service
  for service in frontend backend agent; do
    if [[ ! -e "$root/$service/.env" ]]; then
      (umask 077; set -o noclobber; cat "$root/$service/.env.example" > "$root/$service/.env")
    fi
  done
}

case "${1:-check}" in
  init-env)
    init_env
    ;;
  setup)
    init_env
    cd "$root"
    uv sync --project agent --locked
    npm ci --prefix frontend
    ;;
  check)
    (
      cd "$root"
      uv run --project agent --locked ruff check --config agent/pyproject.toml agent evals
      uv run --project agent --locked ruff format --config agent/pyproject.toml --check agent evals
      uv run --project agent --locked python -m pytest agent/tests evals/tests
      npm run --prefix frontend lint
      npm run --prefix frontend typecheck
      npm run --prefix frontend build
    )
    for script in ./*.sh; do bash -n "$script"; done
    export FRONTEND_ENV_FILE="$root/frontend/.env.example"
    export BACKEND_ENV_FILE="$root/backend/.env.example"
    export AGENT_ENV_FILE="$root/agent/.env.example"
    docker compose --env-file .env.example -f compose.yaml -f compose.dev.yaml config --quiet
    docker compose --env-file .env.example -f compose.yaml config --quiet
    ;;
  *)
    echo 'Использование: deploy/dev.sh {init-env|setup|check}' >&2
    exit 2
    ;;
esac
