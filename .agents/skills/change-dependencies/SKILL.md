---
name: change-dependencies
description: Добавление, удаление или обновление Go, Python, npm или контейнерной зависимости с проверками NETI Next.
---

1. Выбери владельца зависимости. Go использует `backend/go.mod`, агент — `agent/pyproject.toml`, UI — `frontend/package.json`.
2. Обнови зависимости штатной командой Go, uv или npm. Проверь версии инструментов в сборочных образах и CI.
3. Выполни соответствующие команды из [deploy/AGENTS.md](../../../deploy/AGENTS.md). Проверь, что production-образ не получил dev-зависимости.
