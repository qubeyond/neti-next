SHELL := /bin/bash
DEV := docker compose -f compose.yaml -f compose.dev.yaml
SERVICES ?=

.PHONY: setup check format dev down logs smoke

setup:
	./dev.sh setup

check:
	./dev.sh check

format:
	cd .. && uv run --project agent --locked ruff format --config agent/pyproject.toml agent

dev:
	./dev.sh init-env
	$(DEV) up -d --build --wait $(SERVICES)
	$(DEV) ps

down:
	$(DEV) down

logs:
	$(DEV) logs -f

smoke:
	./smoke.sh
