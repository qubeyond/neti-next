"""Проверяет допустимость ответа перед передачей клиенту."""

import re
from decimal import Decimal
from urllib.parse import urlsplit

from agent.domain.recovery import document_key
from agent.domain.tables import AGGREGATE, TOTAL

_BULLET = re.compile(r"^(?:[-*+]|[1-9]\d*\.)[ \t]+[^\n]+$", re.M)
_CITATION_REF = re.compile(r"〔([1-9][0-9]{0,2})〕|\[([1-9][0-9]{0,2})\](?![([])")
_NUMBER_TEXT = r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?"
_CRITICAL_NUMBER = re.compile(
    rf"(?:[₽€$£]\s*({_NUMBER_TEXT})|(?<!\d)"
    rf"(?:({_NUMBER_TEXT})\s*(?:[-–—]|до)\s*)?({_NUMBER_TEXT})\s*"
    r"(?:₽|€|\$|£|USD\b|EUR\b|GBP\b|RUB\b|руб\w*|евро\b|доллар\w*|фунт\w*|"
    r"мест\b|балл\w*|студент\w*|человек\w*|участник\w*|минут\w*|час\w*|дн(?:я|ей)\b|"
    r"день\b|%|тыс\.|января|"
    r"февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря))",
    re.IGNORECASE,
)
_NUMBER = re.compile(_NUMBER_TEXT)
_ENGLISH_GROUPED_AMOUNT = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d,.])")


_ACCOUNT = re.compile(r"\b(?:парол\w*|уч[её]тн\w*\s+запис\w*|аккаунт\w*)\b", re.I)
_AUTH_HOST = {"auth", "account", "accounts", "login", "password", "pwd", "sso", "id"}
_ANSWER_URL = re.compile(r"https?://[^\s<>]+")
_CHANGE = re.compile(r"\b(?:измен|меня|корректир|исправ|редактир)\w*", re.I)
_APPLICATION = re.compile(r"\bзаявлен\w*", re.I)
_LETTER_REQUEST = re.compile(r"\b(?:каким|какое) (?:почтовым )?письм\w*\b", re.I)
_LETTER_KIND = re.compile(r"\b(?:заказн\w*|простым|простое|ценн\w*|описью|уведомлением)\b", re.I)


_ADMISSION = re.compile(
    r"\b(?:до как(?:ой|ого|их) (?:дат\w*|числ\w*)|когда|срок\w*|дедлайн\w*|период\w*)\b",
    re.I,
)
_APPLICATION_TIMING = re.compile(r"\b(?:прием|пода|документ|поступ|раунд)\w*\b", re.I)
_CAMPAIGN_YEAR = re.compile(r"\b20\d{2}\b")
_DATE_CLAIM = re.compile(
    r"\b(?:\d{1,2}[./]\d{1,2}|\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря))\b",
    re.I,
)
_GENERAL_DATES = re.compile(r"\b(?:обычно|обычные сроки|общие сроки)\b", re.I)


def _missing_campaign(question: str, result: dict, citations: list[dict]) -> str:
    years = set(_CAMPAIGN_YEAR.findall(question))

    if len(years) != 1 or not admission_timing(question):
        return ""

    if not _DATE_CLAIM.search(result["answer"]) and not result.get("events"):
        return ""

    year = next(iter(years))
    sources = {source.get("source"): source for source in (result.get("sources") or [])}

    for citation in citations:
        quote = citation["quote"]
        source = sources.get(citation.get("source"), {})
        text = source.get("text", "")
        position = text.find(quote)
        nearby = text[max(0, position - 220) : position + len(quote) + 120] if position >= 0 else ""
        locator = f"{source.get('title', '')} {source.get('url', '')}"

        if year in _CAMPAIGN_YEAR.findall(f"{locator} {quote} {nearby}"):
            return ""

    return year


def admission_timing(question: str) -> bool:
    return bool(
        _ADMISSION.search(question) and _APPLICATION_TIMING.search(question.replace("ё", "е"))
    )


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

    cited = set()

    for citation in citations:
        evidence = citation["quote"]

        cited.update(normal(number) for number in _NUMBER.findall(evidence))

        cited.update(
            number.replace(",", "") for number in _ENGLISH_GROUPED_AMOUNT.findall(evidence)
        )

    calculated = {Decimal(item["result"]) for item in blocks if item.get("kind") == "calculation"}

    claims = (
        normal(number) for match in _CRITICAL_NUMBER.findall(answer) for number in match if number
    )

    return all(number in cited or Decimal(number) in calculated for number in claims)


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


def _inline_refs(answer: str) -> set[int]:
    return {int(number or legacy) for number, legacy in _CITATION_REF.findall(answer)}


def _answer_links(text: str) -> set[str]:
    return {
        address.rstrip(".,!?)]") for address in _ANSWER_URL.findall(_CITATION_REF.sub(" ", text))
    }


def check_answer(question: str, result: dict) -> dict:
    status = result["status"]

    if status == "unverified":
        if result.get("grounding_failure") in {
            "missing_total",
            "citations",
            "conflicting_login_links",
            "procedure",
            "audit",
            "campaign",
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
    refs = {item["ref"] for item in citations if item.get("ref")}
    used_refs = _inline_refs(result["answer"])

    if used_refs and used_refs <= refs and not (result.get("blocks") or result.get("events")):
        citations = [item for item in citations if item.get("ref") in used_refs]
        result = {**result, "citations": citations}

    sources = result.get("sources") or []
    linked = _answer_links(result["answer"])
    source_links = {source.get("url") for source in sources} | {
        url for source in sources for url in _answer_links(source.get("text", ""))
    }

    source_links.update(item.get("url") for item in citations)

    trusted = (
        citations
        and not result["malformed_citations"]
        and used_refs <= (refs or set(range(1, len(citations) + 1)))
        and all(_public_reference(url) and url in source_links for url in linked)
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

    if trusted and (campaign := _missing_campaign(question, result, citations)):
        return {
            **result,
            "status": "unverified",
            "answer": (
                f"Не удалось подтвердить срок приёма документов для кампании {campaign} года."
            ),
            "citations": [],
            "blocks": [],
            "events": [],
            "grounding_failure": "campaign",
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


def supported_list(question: str, draft: dict) -> dict | None:
    items = _BULLET.findall(draft["answer"])

    if len(items) < 2 or draft.get("blocks") or draft.get("events"):
        return None

    verified = {
        item["ref"] for item in draft["citations"] if item.get("verified") and "ref" in item
    }
    retained = []
    used_refs = set()

    for item in items:
        refs = _inline_refs(item)

        if not refs or not refs <= verified:
            continue

        checked = check_answer(
            question,
            {
                **draft,
                "status": "partial",
                "answer": item,
                "malformed_citations": 0,
                "citations": [
                    citation for citation in draft["citations"] if citation.get("ref") in refs
                ],
            },
        )

        if checked["status"] == "partial":
            retained.append(checked)
            used_refs.update(refs)

    if not retained:
        return None

    return {
        **retained[0],
        "answer": "\n".join(item["answer"] for item in retained),
        "citations": [cite for cite in draft["citations"] if cite.get("ref") in used_refs],
        "follow_up": draft.get("follow_up") or "Какой вариант тебе интереснее уточнить?",
    }


def is_truncated(result: dict) -> bool:
    return (result.get("generation") or {}).get("finish_reason") in {"length", "max_tokens"}


def keep_web_evidence(question: str, result: dict) -> dict:
    if not result["citations"]:
        return result

    paragraphs = re.split(r"\n+", result["answer"])
    citations = {item["ref"]: item for item in result["citations"]}
    source_titles = {item["source"]: item["title"] for item in result.get("sources", [])}
    years = set(_CAMPAIGN_YEAR.findall(question))
    kept = []

    for paragraph in paragraphs:
        refs = _inline_refs(paragraph)

        used = [citations[ref] for ref in refs if ref in citations]
        general_dates = bool(
            result["status"] == "partial"
            and result.get("gap")
            and _GENERAL_DATES.search(paragraph)
            and not _CAMPAIGN_YEAR.search(paragraph)
        )

        if (
            len(years) == 1
            and admission_timing(question)
            and _DATE_CLAIM.search(paragraph)
            and not general_dates
        ):
            used = [
                item
                for item in used
                if years
                <= set(
                    _CAMPAIGN_YEAR.findall(
                        f"{source_titles.get(item['source'], '')} {item['quote']}"
                    )
                )
            ]

        if (
            refs
            and len(used) == len(refs)
            and _supported_numbers(paragraph, used, result.get("blocks", []))
            and (
                general_dates
                or not _missing_campaign(question, {**result, "answer": paragraph}, used)
            )
        ):
            text = re.sub(r"\[([^\]\n]+)\]\(https?://[^)\n]+\)", r"\1", paragraph)
            kept.append(_ANSWER_URL.sub("", text).strip())
        elif paragraph.strip().endswith("?") and not refs and not _ANSWER_URL.search(paragraph):
            kept.append(paragraph.strip())

    answer = "\n\n".join(kept)
    refs = _inline_refs(answer)

    return {
        **result,
        "status": "partial"
        if len(kept) < len([line for line in paragraphs if line.strip()])
        else result["status"],
        "answer": answer if refs else "",
        "citations": [item for ref, item in citations.items() if ref in refs],
        "events": [event for event in result.get("events", []) if event["citation"] in refs],
        "blocks": result.get("blocks", []) if refs == set(citations) else [],
    }
