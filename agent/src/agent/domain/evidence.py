"""Нормализует доказательства и сверяет цитаты с источниками."""

import json
import re
from html import unescape
from typing import Literal
from unicodedata import normalize

from pydantic import BaseModel, StrictInt, StrictStr, ValidationError

from agent.domain.blocks import verified_blocks
from agent.domain.calendar import calendar_events
from agent.domain.passages import prefix_with_evidence
from agent.domain.sources import SearchResult
from agent.domain.tables import table_excerpt

_GROUPED_RUBLE_AMOUNT = re.compile(
    r"(?<!\d)\d{1,3}(?: \d{3})+(?= *(?:₽|руб(?:лей|ля|ль|\.)?(?!\w)))",
    re.IGNORECASE,
)


class GeneratedAnswer(BaseModel):
    status: Literal["answered", "partial", "unverified"]
    answer: StrictStr
    citations: list


class GeneratedCitation(BaseModel):
    source: StrictInt
    quote: StrictStr


def json_object(text: str) -> dict:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.IGNORECASE)
    value = json.loads(text)

    if not isinstance(value, dict):
        raise TypeError("Expected JSON object")

    return value


def _plain(text: str) -> str:
    return " ".join(normalize("NFKC", unescape(re.sub(r"</?\w+[^>]*>", "", text))).split())


def _citation_text(text: str) -> str:
    plain = _plain(text).replace(r"\-", "-")
    return re.sub(r"(?<!\*)\*\*(?=\S)([^*]+?)(?<=\S)\*\*(?!\*)", r"\1", plain)


def _find_excerpt(text: str, excerpt: str, offset: int = 0) -> int:
    starts_with_digit = excerpt[0].isdecimal()
    ends_with_digit = excerpt[-1].isdecimal()
    position = text.find(excerpt, offset)

    if not (starts_with_digit or ends_with_digit) or position < 0:
        return position

    amounts = [match.span() for match in _GROUPED_RUBLE_AMOUNT.finditer(text)]

    while position >= 0:
        end = position + len(excerpt)
        starts_inside_number = starts_with_digit and position > 0 and text[position - 1].isdecimal()
        ends_inside_number = ends_with_digit and end < len(text) and text[end].isdecimal()
        splits_amount = any(
            (starts_with_digit and first < position < last)
            or (ends_with_digit and first < end < last)
            for first, last in amounts
        )

        if not starts_inside_number and not ends_inside_number and not splits_amount:
            return position

        position = text.find(excerpt, position + 1)

    return -1


def _verified_excerpts(quote: str, text: str) -> list[str]:
    if not quote:
        return []

    if _find_excerpt(text, quote) >= 0:
        return [quote]

    normalized_quote = _citation_text(quote)
    normalized_text = _citation_text(text)

    if normalized_quote and _find_excerpt(normalized_text, normalized_quote) >= 0:
        return [quote]

    excerpts = re.split(r"\s+\.{3}\s+", quote)

    if len(excerpts) < 2:
        return []

    offset = 0

    for excerpt in excerpts:
        fragment = _citation_text(excerpt)

        if not fragment or (position := _find_excerpt(normalized_text, fragment, offset)) < 0:
            return []

        offset = position + len(fragment)

    return excerpts


def prepare_sources(results: list[SearchResult], limit: int, *, question: str = "") -> list[dict]:
    sources = []

    for index, item in enumerate(results, start=1):
        text = _plain(item["snippet"])

        if not text:
            continue

        if question and len(text) > limit:
            table = _plain(table_excerpt(item["snippet"], question, limit))
            text = (
                table
                if table and len(table) <= limit
                else prefix_with_evidence(text, question, limit, rank_clauses=True)
            )

        sources.append(
            {
                "source": index,
                "url": item["url"],
                "title": _plain(item["title"]),
                "text": text[:limit],
            }
        )

    return sources


def parse_answer(text: str, sources: list[dict]) -> dict:
    try:
        payload = json_object(text)
        value = GeneratedAnswer.model_validate(payload)

        if not value.answer.strip():
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return {
            "status": "invalid",
            "answer": "",
            "citations": [],
            "malformed_citations": 0,
        }

    by_number = {item["source"]: item for item in sources}
    checked = []
    malformed = 0

    for ref, item in enumerate(value.citations, start=1):
        try:
            citation = GeneratedCitation.model_validate(item)
        except ValidationError:
            malformed += 1
            continue

        source = by_number.get(citation.source)
        quote = _plain(citation.quote)
        excerpts = _verified_excerpts(quote, source["text"]) if source else []

        checked.extend(
            {
                "source": citation.source,
                "url": source["url"] if source else None,
                "quote": excerpt,
                "verified": bool(excerpts),
                "ref": ref,
                **(
                    {"attachment_id": source["attachment_id"]}
                    if source and "attachment_id" in source
                    else {}
                ),
            }
            for excerpt in excerpts or [quote]
        )

    events = payload.get("events")
    blocks = verified_blocks(payload.get("blocks"), checked)

    return {
        "status": value.status,
        "answer": value.answer,
        "citations": checked,
        "malformed_citations": malformed,
        **({"blocks": blocks} if blocks else {}),
        **(
            {"events": calendar_events(events, checked)}
            if isinstance(events, list) and events
            else {}
        ),
    }
