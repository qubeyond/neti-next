"""Читает настройки агента из окружения и локального файла."""

import os
from pathlib import Path

from .errors import AgentError

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _local_settings() -> dict[str, str]:
    try:
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise AgentError("Не удалось прочитать файл конфигурации агента.") from None

    settings = {}

    for line in lines:
        name, separator, value = line.partition("=")
        name = name.strip()

        if not separator or not name or name.startswith("#"):
            continue

        value = value.strip()

        if len(value) >= 2 and value[0] in {"'", '"'} and value[0] == value[-1]:
            value = value[1:-1]

        settings[name] = value

    return settings


def _setting(settings: dict[str, str], name: str) -> str:
    value = (os.getenv(name, "").strip() or settings.get(name, "")).strip()

    if not value:
        raise AgentError(f"Укажите {name} в окружении агента или в agent/.env.")

    return value


def _positive_int(settings: dict[str, str], name: str) -> int:
    try:
        value = int(_setting(settings, name))
    except ValueError:
        value = 0

    if value <= 0:
        raise AgentError(f"{name} должен быть положительным целым числом.")

    return value
