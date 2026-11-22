"""Загружает настройки агента и валидирует используемые параметры."""

import os
from contextvars import ContextVar
from pathlib import Path

from dotenv import dotenv_values

from agent.domain.errors import AgentError

CONFIG_DIRECTORY = Path(os.getenv("AGENT_CONFIG_DIRECTORY") or Path(__file__).resolve().parents[2])
ENV_FILE = CONFIG_DIRECTORY / ".env"
DEFAULT_ENV_FILE = CONFIG_DIRECTORY / ".env.example"
SKILLS_DIRECTORY = CONFIG_DIRECTORY / "skills"
DEFAULT_PROMPTS_DIRECTORY = CONFIG_DIRECTORY / "prompts"
PROMPTS_DIRECTORY = Path(
    os.environ.get("AGENT_PROMPTS_DIRECTORY") or CONFIG_DIRECTORY / ".private" / "prompts"
)
PROMPT_NAMES = (
    "TEXT_SYSTEM_PROMPT",
    "DIALOGUE_ROUTING_PROMPT",
    "DECISION_ROUTING_PROMPT",
    "CASUAL_REPLY_PROMPT",
    "DISCOVERY_ROUTING_PROMPT",
    "DISCOVERY_ANSWER_PROMPT",
    "WEB_SYSTEM_PROMPT",
    "WEB_SOURCE_ANSWER_PROMPT",
    "WEB_ANSWER_FORMAT_PROMPT",
    "WEB_REWRITE_PROMPT",
    "WEB_TABLE_PROMPT",
    "WEB_ATTACHMENT_PROMPT",
    "EXA_SEARCH_OBJECTIVE",
    "EVIDENCE_AUDIT_PROMPT",
    "EVIDENCE_REFERENCE_PROMPT",
)
MAX_SEARCH_RESULTS = 20
USER_INSTRUCTIONS: ContextVar[str] = ContextVar("user_instructions", default="")


def _load_prompts(*directories: Path) -> dict[str, str]:
    prompts = {}

    for directory in directories:
        for name in PROMPT_NAMES:
            path = directory / f"{name.lower().replace('_', '-')}.txt"

            try:
                prompts[name] = path.read_text(encoding="utf-8-sig").strip()
            except FileNotFoundError:
                continue

    return prompts


def load_settings(path: Path = ENV_FILE) -> dict[str, str]:
    try:
        values = {}

        if path == ENV_FILE:
            values.update(dotenv_values(DEFAULT_ENV_FILE, encoding="utf-8-sig", interpolate=False))

        values.update(dotenv_values(path, encoding="utf-8-sig", interpolate=False))

        if path == ENV_FILE:
            values.update(_load_prompts(DEFAULT_PROMPTS_DIRECTORY, PROMPTS_DIRECTORY))
        elif path == DEFAULT_ENV_FILE:
            values.update(_load_prompts(DEFAULT_PROMPTS_DIRECTORY))
    except (OSError, UnicodeError):
        raise AgentError("Не удалось прочитать файл конфигурации агента.") from None

    return {key: value for key, value in values.items() if value is not None}


def product_prompt(instructions: str) -> str:
    path = DEFAULT_PROMPTS_DIRECTORY / "product-role-prompt.txt"

    try:
        role = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise AgentError("Не удалось прочитать описание роли Пигеона.") from None

    return f"{role}\n\n{instructions}"


def optional(settings: dict[str, str], name: str) -> str | None:
    return os.environ.get(name, settings.get(name, "")).strip() or None


def required(settings: dict[str, str], name: str) -> str:
    if value := optional(settings, name):
        return value

    location = "agent/.private/prompts/" if name in PROMPT_NAMES else "agent/.env"

    raise AgentError(f"Укажите {name} в окружении агента или в {location}.")


def positive_int(settings: dict[str, str], name: str) -> int:
    try:
        value = int(required(settings, name))
    except ValueError:
        value = 0

    if value <= 0:
        raise AgentError(f"{name} должен быть положительным целым числом.")

    return value


def flag(settings: dict[str, str], name: str) -> bool:
    value = required(settings, name).lower()

    if value not in {"true", "false"}:
        raise AgentError(f"{name} должен быть true или false.")

    return value == "true"


def search_limit(settings: dict[str, str]) -> int:
    limit = positive_int(settings, "SEARCH_MAX_RESULTS")

    if limit > MAX_SEARCH_RESULTS:
        raise AgentError(f"SEARCH_MAX_RESULTS не должен превышать {MAX_SEARCH_RESULTS}.")

    return limit
