"""Записывает коррелированные события инференса без содержимого запросов."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from math import isfinite
from threading import Event

LOGGER = logging.getLogger("neti.inference")
DISCONNECTED: ContextVar[Event | None] = ContextVar("disconnected", default=None)
DEADLINE: ContextVar[float | None] = ContextVar("deadline", default=None)
REQUEST_ID: ContextVar[str | None] = ContextVar("inference_request_id", default=None)
USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "seconds",
    "cost",
    "cached_input_tokens",
    "reasoning_tokens",
)


def configure_logging(level: str) -> None:
    if not LOGGER.handlers:
        LOGGER.addHandler(logging.StreamHandler(sys.stdout))

    LOGGER.setLevel(level)

    LOGGER.propagate = False


def event(name: str, level: int = logging.INFO, **fields) -> None:
    payload = {"ts": datetime.now(UTC).isoformat(), "event": name, **fields}

    if request_id := REQUEST_ID.get():
        payload["request_id"] = request_id

    LOGGER.log(level, "%s", json.dumps(payload, ensure_ascii=False))


def numeric_usage(usage: dict) -> dict[str, int | float]:
    enriched = dict(usage)

    for source, key, counter in (
        ("prompt_tokens_details", "cached_tokens", "cached_input_tokens"),
        ("completion_tokens_details", "reasoning_tokens", "reasoning_tokens"),
    ):
        details = usage.get(source)

        if isinstance(details, dict):
            enriched[counter] = details.get(key)

    return {
        key: value
        for key in USAGE_FIELDS
        if isinstance(value := enriched.get(key), (int, float))
        and not isinstance(value, bool)
        and 0 <= value
        and isfinite(value)
    }
