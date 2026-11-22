"""Передаёт идентификатор запроса и оставшееся время внешним адаптерам."""

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from time import monotonic, perf_counter, time
from uuid import uuid4

from agent.domain.errors import AgentError
from agent.trace import PARENT_SPAN, TRACE

REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="")
DISCONNECTED: ContextVar[Event | None] = ContextVar("disconnected", default=None)
DEADLINE: ContextVar[float | None] = ContextVar("deadline", default=None)
_TRACE_ID = re.compile(r"[0-9a-f]{32}")


def request_id(value: str) -> str:
    return value if _TRACE_ID.fullmatch(value) else uuid4().hex


def deadline(value: str, maximum: int) -> float:
    remaining = maximum

    if value.isascii() and value.isdecimal() and len(value) <= 13:
        remaining = min(maximum, int(value) / 1000 - time())

    return monotonic() + remaining


def remaining_time(maximum: float) -> float:
    disconnected = DISCONNECTED.get()

    if disconnected is not None and disconnected.is_set():
        raise AgentError("Потребитель отключился.", status=504)

    end = DEADLINE.get()
    remaining = min(maximum, end - monotonic()) if end is not None else maximum

    if remaining <= 0:
        raise AgentError("Бюджет времени исчерпан.", status=504)

    return remaining


def upstream_headers() -> dict[str, str]:
    identifier = REQUEST_ID.get()
    end = DEADLINE.get()
    headers = {"X-Request-ID": identifier} if identifier else {}

    if end is not None:
        headers["X-Deadline-Ms"] = str(int((time() + end - monotonic()) * 1000))

    return headers


@contextmanager
def phase(name: str, *, service: str = "agent"):
    started = perf_counter()
    outcome = "ok"
    metadata = {}
    emit = TRACE.get()
    scope = None
    span = ""

    if emit:
        span = uuid4().hex[:16]

        emit(
            {
                "type": "span_start",
                "id": span,
                "parent": PARENT_SPAN.get(),
                "operation": name,
                "service": service,
            }
        )

        scope = PARENT_SPAN.set(span)

    try:
        yield metadata
    except Exception as error:
        outcome = type(error).__name__

        if isinstance(error, AgentError):
            metadata["http_status"] = error.status

        raise
    finally:
        latency = round((perf_counter() - started) * 1000)

        if scope is not None and emit is not None:
            PARENT_SPAN.reset(scope)
            emit(
                {
                    "type": "span_end",
                    "id": span,
                    "duration_ms": latency,
                    "outcome": "ok" if outcome == "ok" else "error",
                    **metadata,
                }
            )

        logging.getLogger("uvicorn.error.agent").info(
            "agent.step request_id=%s phase=%s outcome=%s latency_ms=%s",
            REQUEST_ID.get(),
            name,
            outcome,
            latency,
        )
