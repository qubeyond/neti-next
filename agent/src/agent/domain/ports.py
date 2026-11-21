"""Порты поискового и модельного ввода-вывода."""

from collections.abc import Callable
from typing import Protocol

from agent.domain.sources import SearchResult

Search = Callable[[str], list[SearchResult]]
SearchFactory = Callable[[], Search]
ReadDocument = Callable[[str], str]
RewriteQuery = Callable[[str, list[SearchResult]], tuple[str, dict | None]]


class GenerateAnswer(Protocol):
    def __call__(
        self, question: str, results: list[SearchResult], /, *, model: str | None = None
    ) -> dict: ...


class Completion(Protocol):
    def __call__(
        self,
        question: str,
        *,
        model: str | None,
        system_prompt: str,
        allow_empty: bool,
        reasoning_enabled: bool | None,
        response_format: dict | None,
    ) -> dict: ...
