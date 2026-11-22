# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM python:3.12.14-slim-trixie AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

FROM python:3.12.14-slim-trixie AS runtime
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/venv/bin:$PATH" PYTHONPATH=/app/src
COPY --from=dependencies /opt/venv /opt/venv
COPY src ./src
COPY .env.example ./.env.example
COPY prompts/ ./prompts/
USER 10001:10001
EXPOSE 8002
CMD ["uvicorn", "inference.app:app", "--host", "0.0.0.0", "--port", "8002", "--no-access-log"]
