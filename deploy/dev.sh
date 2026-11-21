#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
root=$(cd .. && pwd)
source ./cache-env.sh

init_env() {
  local service
  for service in frontend backend agent inference; do
    if [[ ! -e "$root/$service/.env" ]]; then
      (umask 077; set -o noclobber; cat "$root/$service/.env.example" > "$root/$service/.env")
    fi
  done
  for service in agent inference; do
    install -d -m 0700 "$root/$service/.private"
    install -d -m 0755 "$root/$service/.private/prompts"
  done
  init_cache_env "$root/agent/.private"
}

case "${1:-check}" in
  init-env)
    init_env
    ;;
  setup)
    init_env
    cd "$root"
    uv sync --project agent --locked
    uv sync --project inference --locked
    npm ci --prefix frontend
    uv run --project agent --locked pre-commit install
    ;;
  check)
    (
      cd "$root"
      uv run --project agent --locked ruff check --config agent/pyproject.toml agent evals inference scripts
      uv run --project agent --locked ruff format --config agent/pyproject.toml --check agent evals inference scripts
      uv run --project agent --locked python scripts/hooks/check_code_style.py
      uv run --project agent --locked python scripts/hooks/check_code_style.py inference
      uv run --project agent --locked pyright --project agent/pyproject.toml
      uv run --project agent --locked pyright --project inference/pyproject.toml --pythonpath inference/.venv/bin/python
      uv run --project agent --locked python -m pytest agent/tests evals/tests
      diff -u docs/api/agent.openapi.json <(uv run --project agent --locked python -c 'import json; from agent.app import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True))')
      (cd inference && PYTHONPATH=src uv run --locked python -m unittest discover -s tests)
      diff -u docs/api/inference.openapi.json <(cd inference && PYTHONPATH=src uv run --locked python -c 'import json; from inference.app import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True))')
      npm run --prefix frontend lint
      npm run --prefix frontend typecheck
      npm run --prefix frontend build
    )
    for script in ./*.sh; do bash -n "$script"; done
    export FRONTEND_ENV_FILE="$root/frontend/.env.example"
    export BACKEND_ENV_FILE="$root/backend/.env.example"
    export AGENT_ENV_FILE="$root/agent/.env.example"
    export INFERENCE_ENV_FILE="$root/inference/.env.example"
    docker compose --env-file .env.example -f compose.yaml -f compose.dev.yaml config --quiet
    docker compose --env-file .env.example -f compose.yaml config --quiet
    docker compose --env-file .env.example -f compose.yaml -f compose.agent-network.yaml config --quiet
    ;;
  *)
    echo 'Использование: deploy/dev.sh {init-env|setup|check}' >&2
    exit 2
    ;;
esac
