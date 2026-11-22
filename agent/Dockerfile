# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.12.14-slim-trixie AS python

FROM python AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 AGENT_CONFIG_DIRECTORY=/app PATH="/opt/venv/bin:$PATH"
COPY --from=dependencies /opt/venv /opt/venv
COPY .env.example ./.env.example
COPY prompts/ ./prompts/
COPY skills/ ./skills/
USER 10001:10001
EXPOSE 8001
CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8001", "--no-access-log"]
