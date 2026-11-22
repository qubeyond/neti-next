"""Извлекает проверяемое наименование вуза для краткой справки."""

import re
from urllib.parse import urlsplit

from agent.domain.evidence import prepare_sources
from agent.domain.recovery import document_key
from agent.domain.sources import SearchResult

_ABBREVIATION = re.compile(r"\b[А-ЯЁA-Z]{2,10}\b")
_FULL_NAME = re.compile(r"\b(?:полностью называ\w*|полное (?:наименование|название))\b", re.I)
_RUSSIAN_NAME = re.compile(
    r"\b([А-ЯЁ][а-яё-]+(?:\s+[а-яё-]+){0,5}\s+(?:университет|институт|академия))"
    r"(?:\s+имени(?:\s*[А-ЯЁ]\.){0,3}\s*[А-ЯЁ][а-яё-]+)?\b"
)
_ENGLISH_NAME = re.compile(
    r"\b((?:[A-Z][a-z]+\s+){1,6}(?:University|Institute|College)"
    r"(?:\s+(?:of|and|for|the|at|[A-Z][a-z]+)){0,5})\b"
)


def institution_fact(question: str, results: list[SearchResult], failed: dict) -> dict | None:
    full_name = _FULL_NAME.search(question) is not None
    abbreviations = set(_ABBREVIATION.findall(question)) - {"ЕГЭ", "РФ", "ИТ"}

    if len(abbreviations) != 1 or (
        not full_name and "overview" not in failed.get("active_skills", ())
    ):
        return None

    abbreviation = next(iter(abbreviations))
    candidates = []

    for source in prepare_sources(results, 4000):
        key = document_key(source["url"])

        if not key or key[0].endswith(("wikipedia.org", "wikidata.org")):
            continue

        for pattern in (_RUSSIAN_NAME, _ENGLISH_NAME):
            for match in pattern.finditer(source["text"]):
                base_name = match[1].removeprefix("The ")
                terms = re.sub(r"\b(?:of|and|for|the|at)\b", " ", base_name).split()
                initials = "".join(word[0].upper() for word in terms)

                if initials == abbreviation:
                    host = urlsplit(source["url"]).hostname or ""
                    priority = (len(match[0]) > len(match[1]), host.endswith((".edu", ".gov.ru")))

                    candidates.append(
                        (priority, base_name.casefold(), source, match[0].removeprefix("The "))
                    )

    if not candidates or len({row[1] for row in candidates}) != 1:
        return None

    _, _, source, name = max(candidates, key=lambda row: row[0])
    citation = {
        "source": source["source"],
        "url": source["url"],
        "quote": name,
        "ref": 1,
        "verified": True,
    }

    return {
        **failed,
        "status": "answered" if full_name else "partial",
        "answer": f"Полное название — {name}〔1〕.",
        "citations": [citation],
        "sources": [source],
        "malformed_citations": 0,
        "blocks": [],
        "events": [],
        "grounding_failure": None,
    }
