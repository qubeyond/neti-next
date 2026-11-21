"""Проверяет допустимость ответа перед передачей клиенту."""

import re
from decimal import Decimal
from urllib.parse import urlsplit

from agent.domain.recovery import document_key
from agent.domain.tables import AGGREGATE, TOTAL

_CRITICAL_NUMBER = re.compile(
    r"(?<!\d)(\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?)\s*"
    r"(?:₽|руб\w*|мест\b|балл\w*|%|тыс\.|января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?")


_ACCOUNT = re.compile(r"\b(?:парол\w*|уч[её]тн\w*\s+запис\w*|аккаунт\w*)\b", re.I)
_AUTH_HOST = {"auth", "account", "accounts", "login", "password", "pwd", "sso", "id"}
_ANSWER_URL = re.compile(r"https?://[^\s<>]+")
_CHANGE = re.compile(r"\b(?:измен|меня|корректир|исправ|редактир)\w*", re.I)
_APPLICATION = re.compile(r"\bзаявлен\w*", re.I)
_LETTER_REQUEST = re.compile(r"\b(?:каким|какое) (?:почтовым )?письм\w*\b", re.I)
_LETTER_KIND = re.compile(r"\b(?:заказн\w*|простым|простое|ценн\w*|описью|уведомлением)\b", re.I)


def _procedure_supported(question: str, answer: str, citations: list[dict]) -> bool:
    quotes = [item.get("quote", "") for item in citations]

    if (
        _CHANGE.search(question)
        and _APPLICATION.search(question)
        and not any(_CHANGE.search(text) and _APPLICATION.search(text) for text in quotes)
    ):
        return False

    return not _LETTER_REQUEST.search(question) or (
        _LETTER_KIND.search(answer) is not None
        and any(_LETTER_KIND.search(text) for text in quotes)
    )


def _conflicting_login_links(question: str, answer: str, sources: list[dict]) -> bool:
    if not _ACCOUNT.search(question):
        return False

    for url in _ANSWER_URL.findall(answer):
        parsed = document_key(url.rstrip(".,!?)]"))

        if parsed is None:
            continue

        host = parsed[0]
        labels = host.split(".")

        if len(labels) < 3 or labels[0] not in _AUTH_HOST:
            continue

        for source in sources:
            candidate = document_key(source.get("url", ""))

            if candidate is not None:
                other = candidate[0].split(".")

                if other[0] != labels[0] and other[0] in _AUTH_HOST and other[1:] == labels[1:]:
                    return True

    return False


def _supported_numbers(answer: str, citations: list[dict], blocks: list[dict]) -> bool:
    def normal(value: str) -> str:
        return "".join(value.split()).replace(",", ".")

    cited = {
        normal(number) for citation in citations for number in _NUMBER.findall(citation["quote"])
    }

    calculated = {Decimal(item["result"]) for item in blocks if item.get("kind") == "calculation"}

    return all(
        normal(number) in cited or Decimal(normal(number)) in calculated
        for number in _CRITICAL_NUMBER.findall(answer)
    )


def _public_reference(url: object) -> bool:
    return (
        isinstance(url, str)
        and document_key(url) is not None
        and not (
            "\\" in url
            or any(character.isspace() for character in url)
            or urlsplit(url).scheme not in {"http", "https"}
        )
    )


def check_answer(question: str, result: dict) -> dict:
    status = result["status"]

    if status == "unverified":
        if result.get("grounding_failure") in {
            "missing_total",
            "citations",
            "conflicting_login_links",
            "procedure",
            "audit",
        }:
            return result

        return {
            **result,
            "answer": "По найденным источникам подтвердить ответ не удалось.",
            "citations": [],
        }

    if status not in {"answered", "partial"}:
        return result

    citations = result["citations"]
    attachment_ids = {item.get("attachment_id") for item in result.get("sources") or []}
    trusted = (
        citations
        and not result["malformed_citations"]
        and all(
            item["verified"]
            and (
                _public_reference(item["url"])
                or (item.get("attachment_id") and item["attachment_id"] in attachment_ids)
            )
            for item in citations
        )
        and _supported_numbers(result["answer"], citations, result.get("blocks") or [])
    )
    sources = result.get("sources") or []
    has_table = any(item["text"].count("|") >= 4 for item in sources)
    needs_total = AGGREGATE.search(question) and has_table
    total_supported = any(TOTAL.search(item.get("quote") or "") for item in citations)

    conflicting_links = _conflicting_login_links(question, result["answer"], sources)

    if conflicting_links:
        return {
            **result,
            "status": "unverified",
            "answer": "Адреса входа различаются. Уточните нужный адрес в поддержке вуза.",
            "citations": [],
            "grounding_failure": "conflicting_login_links",
        }

    procedure_supported = status == "partial" or _procedure_supported(
        question, result["answer"], citations
    )

    if trusted and not procedure_supported:
        return {
            **result,
            "status": "unverified",
            "answer": "Найденные источники не подтверждают нужную процедуру.",
            "citations": [],
            "grounding_failure": "procedure",
        }

    if trusted and (not needs_total or status == "partial" or total_supported):
        return result

    message = (
        "По найденным фрагментам общий итог не подтверждён."
        if trusted and needs_total
        else "По найденным фрагментам ответ подтвердить не удалось."
    )

    return {
        **result,
        "status": "unverified",
        "answer": message,
        "citations": [],
        "grounding_failure": "missing_total" if trusted else "citations",
    }


def is_truncated(result: dict) -> bool:
    return (result.get("generation") or {}).get("finish_reason") in {"length", "max_tokens"}
