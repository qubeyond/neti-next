"""Выбирает поисковик и нормализует его выдачу."""

from collections.abc import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from httpx2 import HTTPError, HTTPStatusError, Request
from lxml import html
from lxml.etree import ParserError

from agent.config import load_settings, positive_int, required, search_limit
from agent.domain.errors import AgentError
from agent.domain.sources import SearchResult
from agent.providers.http import client, send_json
from agent.providers.search_apis import ALTERNATIVES, SearchBlocked, make_api_searcher
from agent.runtime import remaining_time

PROVIDERS = ("tavily", "duckduckgo", *ALTERNATIVES)
DUCKDUCKGO_ENDPOINT = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_REGION = "ru-ru"


def _normalize(items: list, url_key: str, snippet_key: str, limit: int) -> list[SearchResult]:
    results = []

    for item in items[:limit]:
        if not isinstance(item, dict) or not isinstance(item.get(url_key), str):
            raise AgentError("Поисковик вернул результат без URL.")

        results.append(
            {
                "title": str(item.get("title") or ""),
                "url": item[url_key],
                "snippet": str(item.get(snippet_key) or ""),
            }
        )

    return results


def make_searcher(
    provider: str | None = None, *, settings: dict[str, str] | None = None
) -> Callable[[str], list[SearchResult]]:
    settings = load_settings() if settings is None else settings
    provider = provider or required(settings, "SEARCH_PROVIDER")

    if provider not in PROVIDERS:
        raise AgentError(f"Неизвестный поисковик: {provider}.")

    limit = search_limit(settings)
    timeout = positive_int(settings, "SEARCH_TIMEOUT_SECONDS")

    if provider in ALTERNATIVES:
        return make_api_searcher(provider, settings, limit, timeout)

    if provider == "tavily":
        api_key = required(settings, "TAVILY_API_KEY")
        depth = required(settings, "TAVILY_SEARCH_DEPTH")
        endpoint = required(settings, "TAVILY_SEARCH_URL")

        if depth not in {"basic", "advanced"}:
            raise AgentError("TAVILY_SEARCH_DEPTH должен быть basic или advanced.")

        def search_tavily(query: str) -> list[SearchResult]:
            payload = {
                "query": query,
                "search_depth": depth,
                "max_results": limit,
                "auto_parameters": False,
                "include_answer": False,
                "include_raw_content": False,
            }

            try:
                request = Request(
                    "POST",
                    endpoint,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                items = send_json(request, timeout)["results"]
            except HTTPStatusError as error:
                status = error.response.status_code

                raise AgentError(
                    "Tavily отклонил запрос.", status=429 if status in {402, 429} else 502
                ) from None
            except HTTPError:
                raise AgentError("Tavily: ошибка сети или таймаут.") from None
            except (ValueError, KeyError, TypeError):
                raise AgentError("Tavily: неверный формат ответа.") from None

            if not isinstance(items, list):
                raise AgentError("Tavily: неверный формат результатов.")

            return _normalize(items, "url", "content", limit)

        return search_tavily

    def search_duckduckgo(query: str) -> list[SearchResult]:
        try:
            response = client.post(
                DUCKDUCKGO_ENDPOINT,
                data={"q": query, "l": DUCKDUCKGO_REGION},
                timeout=remaining_time(timeout),
                follow_redirects=False,
            )

            response.raise_for_status()

            status, document = response.status_code, response.content
        except HTTPStatusError as error:
            status = error.response.status_code

            if status in {403, 429}:
                raise SearchBlocked(f"DuckDuckGo: доступ ограничен, HTTP {status}.") from None

            raise AgentError(f"DuckDuckGo: HTTP {status}.") from None
        except HTTPError:
            raise AgentError("DuckDuckGo: ошибка сети или таймаут.") from None

        if b"anomaly-modal" in document or b"challenge-form" in document:
            raise SearchBlocked(f"DuckDuckGo: проверка на бота, HTTP {status}.")

        if status != 200:
            raise AgentError(f"DuckDuckGo: вместо выдачи получен HTTP {status}.")

        try:
            page = html.fromstring(document)
        except (ValueError, ParserError):
            raise AgentError("DuckDuckGo: не удалось разобрать HTML.") from None

        results = []

        for result in page.find_class("result"):
            anchors = result.find_class("result__a")

            if not anchors or not anchors[0].get("href"):
                continue

            anchor = anchors[0]

            try:
                url = urljoin(DUCKDUCKGO_ENDPOINT, anchor.get("href"))
                parts = urlsplit(url)

                if (
                    parts.hostname in {"duckduckgo.com", "html.duckduckgo.com"}
                    and parts.path == "/l/"
                ):
                    url = parse_qs(parts.query).get("uddg", [url])[0]
                    parts = urlsplit(url)

                if parts.scheme not in {"http", "https"} or not parts.hostname:
                    continue
            except ValueError:
                continue

            snippets = result.find_class("result__snippet")

            results.append(
                {
                    "title": " ".join(anchor.text_content().split()),
                    "url": url,
                    "snippet": " ".join(snippets[0].text_content().split()) if snippets else "",
                }
            )

            if len(results) == limit:
                break

        if (
            not results
            and not page.find_class("no-results")
            and not page.find_class("result--no-result")
        ):
            raise AgentError("DuckDuckGo: в HTML нет знакомого блока выдачи.")

        return results

    return search_duckduckgo
