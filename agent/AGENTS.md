# Агент

Заготовка самостоятельного Python-сервиса.

- Храни исходники в `src/agent/`, зависимости — в `pyproject.toml`, настройки — в `.env`.
- Маршрут `GET /agent/health` используют Docker и CI.
- Из этой папки запускай `uv sync --locked` и `uv run --locked python -m pytest`.
- Проверяй самостоятельную сборку командой `docker build -t neti-agent .`.
