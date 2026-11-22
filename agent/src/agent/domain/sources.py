"""Форма поискового результата, используемая внутри агента."""

from typing import TypedDict
from urllib.parse import urlsplit


class SearchResult(TypedDict):
    url: str
    title: str
    snippet: str


_SHARED_CHAT_PATHS = {
    "chatgpt.com": ("/share/",),
    "claude.ai": ("/share/",),
    "gemini.google.com": ("/share/",),
}


def evidence_sources(results: list[SearchResult]) -> list[SearchResult]:
    sources = []

    for result in results:
        try:
            url = urlsplit(result["url"])
        except ValueError:
            continue

        host = (url.hostname or "").removeprefix("www.").rstrip(".")
        prefixes = _SHARED_CHAT_PATHS.get(host, ())

        if host != "chat01.ai" and not url.path.startswith(prefixes):
            sample = result["snippet"][:500]
            encoded = sum(0x80 <= ord(character) <= 0xFF for character in sample)
            breaks = sum(character.isspace() for character in sample)
            unreadable = (
                len(sample) >= 80 and encoded * 3 > len(sample) and breaks * 20 < len(sample)
            )

            sources.append({**result, "snippet": ""} if unreadable else result)

    return sources
