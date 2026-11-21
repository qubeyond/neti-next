"""Сохраняет заголовок и итоговые строки таблиц при ограничении контекста."""

import re
from itertools import groupby

from agent.domain.passages import QUERY_STOP_WORDS, WORD

AGGREGATE = re.compile(
    r"\b(?:всего|итого|суммар\w*|общ\w*\s+(?:количеств\w*|числ\w*|сумм\w*|вместимост\w*))\b", re.I
)
DIVIDER = re.compile(r"\s*\|(?:\s*:?-+:?\s*\|)+\s*")
TOTAL = re.compile(r"\b(?:итого|всего|суммарно)\b", re.I)
IDENTIFIER = re.compile(r"№\s*(\d+(?:/\d+)?)|\b(\d{2}\.\d{2}\.\d{2})\b")


def _row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 3


def _total(line: str) -> bool:
    cells = [cell.strip() for cell in line.split("|")[1:-1]]
    first = next((cell for cell in cells if cell), "")

    return bool(TOTAL.fullmatch(first.strip("* _:.")))


def _terms(text: str) -> set[str]:
    return {
        word[:5]
        for word in WORD.findall(text.lower())
        if len(word) >= 4 and word not in QUERY_STOP_WORDS
    }


def table_excerpt(text: str, question: str, limit: int) -> str:
    if not AGGREGATE.search(question) or "|" not in text:
        return ""

    terms = _terms(question)
    identifiers = {first or second for first, second in IDENTIFIER.findall(question)}
    ranked = []
    heading = ""

    for is_table, group in groupby((line.strip() for line in text.splitlines()), _row):
        lines = list(group)

        if not is_table:
            heading = next((line for line in reversed(lines) if line), heading)
            continue

        if len(lines) < 3 or not DIVIDER.fullmatch(lines[1]):
            continue

        totals = {index for index in range(2, len(lines)) if _total(lines[index])}
        relevance = len(terms & _terms(f"{heading} {lines[0]}"))

        if relevance:
            ranked.append((relevance, heading, lines, totals))

    if len(text) <= limit and any(totals for _, _, _, totals in ranked):
        return text

    ranked.sort(key=lambda candidate: candidate[0], reverse=True)

    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return ""

    _, heading, lines, totals = ranked[0]

    if not totals:
        return ""

    selected = {0, 1, *totals}
    heading = heading if len(heading) <= limit // 5 else ""
    remaining = limit - sum(len(lines[index]) + 1 for index in selected) - len(heading)

    if remaining < 0:
        return ""

    def row_priority(index: int) -> tuple[int, int]:
        line = lines[index]
        numbers = set(re.findall(r"\b\d+(?:[./]\d+)*\b", line))

        return len(identifiers & numbers), len(terms & _terms(line))

    rows = sorted(
        (index for index in range(2, len(lines)) if index not in totals),
        key=row_priority,
        reverse=True,
    )

    for index in rows:
        size = len(lines[index]) + 1

        if size <= remaining:
            selected.add(index)
            remaining -= size

    return "\n".join(filter(None, [heading, *(lines[index] for index in sorted(selected))]))
