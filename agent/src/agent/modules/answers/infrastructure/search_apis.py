"""Адаптирует HTTP-контракты поисковых провайдеров."""

import json
from collections.abc import Callable
from html import unescape
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from agent.shared.config import _setting
from agent.shared.errors import AgentError

from ..domain.sources import SearchResult

API_KEYS = {
    "brave": "BRAVE_SEARCH_API_KEY",
    "serper": "SERPER_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "exa": "EXA_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "linkup": "LINKUP_API_KEY",
    "langsearch": "LANGSEARCH_API_KEY",
}
KEYLESS = ("mwmbl", "wiby", "marginalia", "firecrawl")
ALTERNATIVES = (*KEYLESS, *API_KEYS, "searxng")
ATTRIBUTION = {"wiby": "https://wiby.me/"}
EXA_SEARCH_OBJECTIVE = (
    "Найди прямые официальные страницы и документы нужного вуза, "
    "которые подтверждают ответ на вопрос. Подбери выдержки с нужными датами, "
    "фактами и условиями. Учитывай филиал, программу и год, если они указаны."
)


class SearchBlocked(AgentError):
    pass


def _get(url: str, params: dict, headers: dict | None = None) -> Request:
    return Request(f"{url}?{urlencode(params)}", headers=headers or {})


def _post(url: str, params: dict, headers: dict) -> Request:
    return Request(
        url,
        data=json.dumps(params).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )


def _optional(settings: dict[str, str], name: str) -> str | None:
    try:
        return _setting(settings, name)
    except AgentError:
        return None


def missing_setting(provider: str, settings: dict[str, str]) -> str | None:
    name = "SEARXNG_URL" if provider == "searxng" else API_KEYS.get(provider)
    return name if name and not _optional(settings, name) else None


def _request(provider: str, query: str, limit: int, key: str, instance: str) -> Request:
    bearer = {"Authorization": f"Bearer {key}"} if key else {}

    if provider == "brave":
        return _get(
            "https://api.search.brave.com/res/v1/web/search",
            {
                "q": query,
                "count": limit,
                "search_lang": "ru",
                "country": "ALL",
                "result_filter": "web",
            },
            {"X-Subscription-Token": key},
        )

    if provider == "serper":
        return _post(
            "https://google.serper.dev/search",
            {
                "q": query,
                "num": limit,
                "gl": "ru",
                "hl": "ru",
            },
            {"X-API-KEY": key},
        )

    if provider == "serpapi":
        return _get(
            "https://serpapi.com/search.json",
            {
                "engine": "google",
                "q": query,
                "gl": "ru",
                "hl": "ru",
                "api_key": key,
            },
        )

    if provider == "exa":
        return _post(
            "https://api.exa.ai/search",
            {
                "query": query,
                "type": "auto",
                "numResults": limit,
                "contents": {"highlights": True},
                "objective": EXA_SEARCH_OBJECTIVE,
            },
            {"x-api-key": key},
        )

    if provider == "parallel":
        return _post(
            "https://api.parallel.ai/v1/search",
            {
                "search_queries": [query],
                "mode": "turbo",
                "advanced_settings": {"max_results": limit},
            },
            {"x-api-key": key},
        )

    if provider == "linkup":
        return _post(
            "https://api.linkup.so/v1/search",
            {
                "q": query,
                "depth": "fast",
                "outputType": "searchResults",
                "maxResults": limit,
            },
            bearer,
        )

    if provider == "firecrawl":
        return _post(
            "https://api.firecrawl.dev/v2/search",
            {
                "query": query,
                "limit": limit,
                "sources": [{"type": "web"}],
            },
            bearer,
        )

    if provider == "langsearch":
        return _post(
            "https://api.langsearch.com/v1/web-search",
            {
                "query": query,
                "count": limit,
            },
            bearer,
        )

    if provider == "mwmbl":
        params = {"q": query, **({"api_key": key} if key else {})}
        return _get("https://api.mwmbl.org/api/v2/search/", params)

    if provider == "wiby":
        return _get("https://wiby.me/json/", {"q": query})

    if provider == "marginalia":
        return _get(
            "https://api2.marginalia-search.com/search",
            {
                "query": query,
                "count": limit,
            },
            {"API-Key": key or "public"},
        )

    if provider == "searxng":
        return _get(f"{instance}/search", {"q": query, "format": "json", "language": "ru"})

    raise AgentError(f"Неизвестный поисковик: {provider}.")


RESPONSE_FIELDS = {
    "brave": (("web", "results"), "url", "title", "description"),
    "serper": (("organic",), "link", "title", "snippet"),
    "serpapi": (("organic_results",), "link", "title", "snippet"),
    "exa": (("results",), "url", "title", "highlights"),
    "parallel": (("results",), "url", "title", "excerpts"),
    "linkup": (("results",), "url", "name", "content"),
    "firecrawl": (("data", "web"), "url", "title", "description"),
    "langsearch": (("data", "webPages", "value"), "url", "name", "snippet"),
    "mwmbl": (("results",), "url", "title", "content"),
    "wiby": ((), "URL", "Title", "Snippet"),
    "marginalia": (("results",), "url", "title", "description"),
    "searxng": (("results",), "url", "title", "content"),
}


def _results(provider: str, payload, limit: int) -> list[SearchResult]:
    if isinstance(payload, dict):
        error_code = str(payload.get("code", "200"))

        if provider == "langsearch" and error_code in {"401", "402", "403", "429"}:
            raise SearchBlocked(f"{provider}: доступ или квота, код {error_code}.")

        if (
            payload.get("error")
            or payload.get("success") is False
            or (provider == "langsearch" and error_code != "200")
        ):
            raise AgentError(f"{provider}: запрос отклонён API.")

        if (
            provider == "serpapi"
            and "organic_results" not in payload
            and (payload.get("search_metadata") or {}).get("status") == "Success"
        ):
            return []

        if provider == "brave" and "web" not in payload and payload.get("type") == "search":
            return []

    path, url_field, title_field, snippet_field = RESPONSE_FIELDS[provider]

    try:
        for field in path:
            payload = payload[field]
    except (KeyError, TypeError):
        raise AgentError(f"{provider}: неизвестный формат ответа.") from None

    if not isinstance(payload, list):
        raise AgentError(f"{provider}: не получен список результатов.")

    results = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        url = item.get(url_field)

        if not isinstance(url, str):
            continue

        try:
            parsed = urlsplit(url)

            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
        except ValueError:
            continue

        snippet = item.get(snippet_field) or ""

        if isinstance(snippet, list):
            pieces = [piece for piece in snippet if isinstance(piece, str)]
            snippet = (
                " … ".join(piece.strip() for piece in pieces if piece.strip())
                if provider == "exa"
                else " ".join(pieces)
            )

        results.append(
            {
                "url": url,
                "title": unescape(str(item.get(title_field) or "")),
                "snippet": unescape(str(snippet)),
            }
        )

        if len(results) == limit:
            break

    if payload and not results:
        raise AgentError(f"{provider}: в выдаче нет распознаваемых веб-ссылок.")

    return results


def make_api_searcher(
    provider: str,
    settings: dict[str, str],
    limit: int,
    timeout: int,
) -> Callable[[str], list[SearchResult]]:
    name = API_KEYS.get(provider)
    key = _setting(settings, name) if name else ""

    if provider in {"mwmbl", "marginalia", "firecrawl"}:
        key = _optional(settings, f"{provider.upper()}_API_KEY") or ""

    instance = _setting(settings, "SEARXNG_URL").rstrip("/") if provider == "searxng" else ""

    if instance:
        try:
            url = urlsplit(instance)
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and not (url.username or url.password or url.query or url.fragment)
            )
        except ValueError:
            valid = False

        if not valid:
            raise AgentError("SEARXNG_URL должен быть базовым HTTP(S)-адресом своего сервера.")

    last_request = None

    def search(query: str) -> list[SearchResult]:
        nonlocal last_request

        if provider in KEYLESS and last_request is not None:
            sleep(max(0.0, 1.05 - (monotonic() - last_request)))

        request = _request(provider, query, limit, key, instance)
        last_request = monotonic()

        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            if error.code in {401, 402, 403, 429} or (
                provider == "marginalia" and error.code == 503
            ):
                raise SearchBlocked(f"{provider}: доступ или квота, HTTP {error.code}.") from None

            raise AgentError(f"{provider}: HTTP {error.code}.") from None
        except (URLError, TimeoutError):
            raise AgentError(f"{provider}: ошибка сети или таймаут.") from None
        except ValueError:
            raise AgentError(f"{provider}: ответ не в формате JSON.") from None

        return _results(provider, payload, limit)

    return search
