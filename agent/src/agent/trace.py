"""Передаёт разрешённые события текущего демонстрационного запроса."""

from collections.abc import Callable
from contextvars import ContextVar

TRACE: ContextVar[Callable[[dict], None] | None] = ContextVar("demo_trace", default=None)
PARENT_SPAN: ContextVar[str] = ContextVar("demo_span", default="agent")
