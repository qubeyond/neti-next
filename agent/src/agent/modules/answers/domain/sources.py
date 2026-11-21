"""Форма поискового результата, используемая внутри агента."""

from typing import TypedDict


class SearchResult(TypedDict):
    url: str
    title: str
    snippet: str
