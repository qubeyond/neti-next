"""Порты поискового и модельного ввода-вывода."""

from collections.abc import Callable
from typing import Literal, Protocol, TypedDict

from agent.domain.sources import SearchResult

Search = Callable[[str], list[SearchResult]]
SearchFactory = Callable[[], Search]
ReadDocument = Callable[[str], str]
RewriteQuery = Callable[[str, list[SearchResult]], tuple[str, dict | None]]


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class GenerateAnswer(Protocol):
    def __call__(
        self, question: str, results: list[SearchResult], /, *, model: str | None = None
    ) -> dict: ...


class Completion(Protocol):
    def __call__(
        self,
        question: str,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        system_prompt: str,
        allow_empty: bool,
        reasoning_enabled: bool | None,
        response_format: dict | None,
        history: list[ChatMessage] | None = None,
        tools: list[dict] | None = None,
    ) -> dict: ...
