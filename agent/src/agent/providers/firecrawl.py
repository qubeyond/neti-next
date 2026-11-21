"""Читает один публичный документ через Firecrawl."""

import json
from urllib.parse import urlsplit

from httpx2 import HTTPError

from agent.config import positive_int, required
from agent.domain.recovery import document_key
from agent.providers.http import client
from agent.runtime import remaining_time

MAX_RESPONSE_BYTES = 2_000_000
MAX_DOCUMENT_CHARS = 150_000


def read_document(url: str, settings: dict[str, str]) -> str:
    key = document_key(url)

    if key is None:
        return ""

    try:
        with client.stream(
            "POST",
            required(settings, "FIRECRAWL_SCRAPE_URL"),
            json={
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
                "timeout": positive_int(settings, "FIRECRAWL_SCRAPE_TIMEOUT_MS"),
            },
            headers={"Authorization": f"Bearer {required(settings, 'FIRECRAWL_API_KEY')}"},
            timeout=remaining_time(positive_int(settings, "FIRECRAWL_HTTP_TIMEOUT_SECONDS")),
        ) as response:
            response.raise_for_status()

            body = bytearray()

            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                remaining_time(1)

                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    return ""

                body.extend(chunk)

            payload = json.loads(body)

        data = payload["data"]
        metadata = data.get("metadata") or {}
        final = metadata.get("url") or metadata.get("sourceURL") or url
        text = data.get("markdown")
    except (HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return ""

    if (
        not isinstance(final, str)
        or document_key(final) != key
        or (urlsplit(url).scheme == "https" and urlsplit(final).scheme != "https")
        or not isinstance(text, str)
    ):
        return ""

    return text[:MAX_DOCUMENT_CHARS]
