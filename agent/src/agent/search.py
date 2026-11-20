import json
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from .config import AgentError, _local_settings, _positive_int, _setting
from .search_apis import ALTERNATIVES, SearchBlocked, SearchResult, make_api_searcher

PROVIDERS = ("tavily", "duckduckgo", *ALTERNATIVES)


def _normalize(
    items: list, url_key: str, snippet_key: str, limit: int
) -> list[SearchResult]:
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


def make_searcher(provider: str | None = None) -> Callable[[str], list[SearchResult]]:
    settings = _local_settings()
    provider = provider or _setting(settings, "SEARCH_PROVIDER")
    if provider not in PROVIDERS:
        raise AgentError(f"Неизвестный поисковик: {provider}.")
    limit = _positive_int(settings, "SEARCH_MAX_RESULTS")
    if limit > 20:
        raise AgentError("SEARCH_MAX_RESULTS не должен превышать 20.")
    timeout = _positive_int(settings, "SEARCH_TIMEOUT_SECONDS")

    if provider in ALTERNATIVES:
        return make_api_searcher(provider, settings, limit, timeout)

    if provider == "tavily":
        api_key = _setting(settings, "TAVILY_API_KEY")
        depth = _setting(settings, "TAVILY_SEARCH_DEPTH")
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
            request = Request(
                "https://api.tavily.com/search",
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    items = json.load(response)["results"]
            except HTTPError as error:
                raise AgentError(f"Tavily: HTTP {error.code}.") from None
            except (URLError, TimeoutError):
                raise AgentError("Tavily: ошибка сети или таймаут.") from None
            except (ValueError, KeyError, TypeError):
                raise AgentError("Tavily: неверный формат ответа.") from None
            if not isinstance(items, list):
                raise AgentError("Tavily: неверный формат результатов.")
            return _normalize(items, "url", "content", limit)

        return search_tavily

    try:
        from lxml import etree, html
    except ImportError:
        raise AgentError(
            "Установите зависимости агента командой uv sync --project agent --locked."
        ) from None
    region = _setting(settings, "DUCKDUCKGO_REGION")
    endpoint = "https://html.duckduckgo.com/html/"

    def search_duckduckgo(query: str) -> list[SearchResult]:
        request = Request(
            endpoint, data=urlencode({"q": query, "l": region}).encode(), method="POST"
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                status = response.status
                document = response.read()
        except HTTPError as error:
            if error.code in {403, 429}:
                raise SearchBlocked(
                    f"DuckDuckGo: доступ ограничен, HTTP {error.code}."
                ) from None
            raise AgentError(f"DuckDuckGo: HTTP {error.code}.") from None
        except (URLError, TimeoutError):
            raise AgentError("DuckDuckGo: ошибка сети или таймаут.") from None
        if b"anomaly-modal" in document or b"challenge-form" in document:
            raise SearchBlocked(f"DuckDuckGo: проверка на бота, HTTP {status}.")
        if status != 200:
            raise AgentError(f"DuckDuckGo: вместо выдачи получен HTTP {status}.")

        try:
            page = html.fromstring(document)
        except (ValueError, etree.ParserError):
            raise AgentError("DuckDuckGo: не удалось разобрать HTML.") from None
        results = []
        for result in page.find_class("result"):
            anchors = result.find_class("result__a")
            if not anchors or not anchors[0].get("href"):
                continue
            anchor = anchors[0]
            try:
                url = urljoin(endpoint, anchor.get("href"))
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
                    "snippet": " ".join(snippets[0].text_content().split())
                    if snippets
                    else "",
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
