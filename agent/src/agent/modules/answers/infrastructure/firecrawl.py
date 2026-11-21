"""Читает один публичный документ через Firecrawl."""

import json
from urllib.error import URLError
from urllib.request import urlopen

from agent.shared.config import _setting

from ..domain.recovery import document_key
from .search_apis import _post

DOCUMENT_LIMIT = 150_000


def read_document(url: str, settings: dict[str, str]) -> str:
    key = document_key(url)

    if key is None:
        return ""

    request = _post(
        "https://api.firecrawl.dev/v2/scrape",
        {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": 10000,
        },
        {"Authorization": f"Bearer {_setting(settings, 'FIRECRAWL_API_KEY')}"},
    )

    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read(2_000_000))

        data = payload["data"]
        metadata = data.get("metadata") or {}
        final = metadata.get("url") or metadata.get("sourceURL") or url
        text = data.get("markdown")
    except (URLError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return ""

    if document_key(final) != key or not isinstance(text, str):
        return ""

    return text[:DOCUMENT_LIMIT]
