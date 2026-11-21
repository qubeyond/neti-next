"""Проверяет даты событий по привязанным цитатам."""

import re
from datetime import date

from pydantic import BaseModel, Field, StrictInt, ValidationError

from agent.domain.recovery import document_key

_MONTHS = (
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря"
).split()
_DEADLINE = re.compile(
    r"\b(?:до|не позднее|по|дедлайн|окончани\w*|завершени\w*|"
    r"заверша\w*|завершит\w*|заканчива\w*|закончит\w*|"
    r"последний день)\s*(?:[:—–-]\s*)?$",
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY = re.compile(r"[!?;]+|\.\s+(?=[А-ЯЁA-Z])")
_UNCERTAIN = re.compile(
    r"\b(?:не|нет|планиру\w*|предварительн\w*|ориентировочн\w*|"
    r"отмен\w*|перенес\w*|перенёс\w*|проект\w*)\b",
    re.I,
)


class CalendarCandidate(BaseModel):
    date: date
    title: str = Field(min_length=1, max_length=160)
    citation: StrictInt = Field(ge=1)


def _ends_on(quote: str, day: date) -> bool:
    forms = (
        re.escape(day.isoformat()),
        rf"0?{day.day}[./]0?{day.month}[./]{day.year}",
        rf"0?{day.day}\s+{_MONTHS[day.month - 1]}\s+{day.year}",
    )
    dates = re.finditer(r"(?<!\d)(?:" + "|".join(forms) + r")(?!\d)", quote, re.I)

    for item in dates:
        before = _CLAUSE_BOUNDARY.split(quote[: item.start()])[-1]
        after = _CLAUSE_BOUNDARY.split(quote[item.end() :], maxsplit=1)[0]
        clause = re.sub(r"\bне позднее\b", "позднее", before + " " + after, flags=re.I)

        if _DEADLINE.search(before.rstrip()) and not _UNCERTAIN.search(clause):
            return True

    return False


def calendar_events(candidates: list, citations: list[dict]) -> list[dict]:
    events = []
    seen = set()

    for raw in candidates[:10]:
        try:
            candidate = CalendarCandidate.model_validate(raw)
        except ValidationError:
            continue

        title = " ".join(candidate.title.split())
        key = (candidate.date, title.casefold())

        if not title or key in seen:
            continue

        for item in citations:
            url = item.get("url")
            quote = item["quote"]

            if (
                item.get("ref") == candidate.citation
                and item["verified"]
                and isinstance(url, str)
                and document_key(url) is not None
                and _ends_on(quote, candidate.date)
            ):
                events.append(
                    {
                        "date": candidate.date.isoformat(),
                        "title": title,
                        "citation": candidate.citation,
                    }
                )
                seen.add(key)

                break

    return events
