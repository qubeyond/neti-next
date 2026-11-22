"""Ограничивает передачу личных данных в поисковые API."""

import re

_PRIVATE = re.compile(
    r"https?://\S+|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b"
    r"|(?<!\w)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)",
    re.IGNORECASE,
)


def public_query(text: str) -> str:
    return " ".join(_PRIVATE.sub(" ", text).split())


_TURN_BOUNDARY = re.compile(r"(?<=\?)\s+|\r?\n+")
_ROLE_LINE = re.compile(r"^\s*(?:\[|<)/?(?:system|developer|assistant|user)(?:\]|>)", re.I)
_DIRECTIVE = re.compile(
    r"\b(?:вывед|подстав|верни|напечатай|повтори|встав|добав|используй|игнорир|забуд)\w*",
    re.I,
)
_META = re.compile(
    r"\b(?:вместо|маркер\w*|строк\w*|служебн\w*|формат\w*|тест\w*|инструкц\w*)\b",
    re.I,
)
_EVIDENCE_OVERRIDE = re.compile(
    r"\b(?:(?:не\s+(?:проверяй|сверяй|используй|учитывай))|игнорируй|отбрось)\s+"
    r"(?:(?:все|эти|данные|найденные|официальные)\s+){0,2}"
    r"(?:источник\w*|цитат\w*|доказательств\w*|факт\w*)\b",
    re.I,
)

_FABRICATED_EVIDENCE = re.compile(
    r"\b(?:припиши(?:те)?|выдумай(?:те)?|сфабрикуй(?:те)?|подделай(?:те)?)\b"
    r"[^?\n]{0,120}\b(?:цитат\w*|выдержк\w*|источник\w*|доказательств\w*)\b"
    r"|\b(?:выдай(?:те)?|представь(?:те)?)\b[^?\n]{0,90}\bкак\s+"
    r"(?:дословн\w*\s+)?(?:цитат\w*|выдержк\w*|доказанн\w+\s+факт)\b",
    re.I,
)


_PRIVATE_PROMPT_REQUEST = re.compile(
    r"\b(?:покажи|процитируй|напечатай|выведи|раскрой|повтори|перескажи|пришли|скопируй)"
    r"\w*\b.{0,70}?"
    r"\b(?:(?:свой|свои|твой|твои|твоя)\s+|(?=(?:системн|служебн|внутренн)))"
    r"(?:(?:системн\w*|служебн\w*|внутренн\w*|защитн\w*)\s+)?"
    r"(?:промпт\w*|инструкц\w*|токен\w*|секрет\w*)\b",
    re.I,
)


_SAFE_FORMAT = re.compile(r"\bв\s+формате\s+(?:таблицы|списка|плана)\b", re.I)


def _untrusted_instruction(text: str, meta: re.Pattern[str]) -> bool:
    return bool(
        _ROLE_LINE.match(text)
        or _EVIDENCE_OVERRIDE.search(text)
        or _FABRICATED_EVIDENCE.search(text)
        or (_DIRECTIVE.search(text) and meta.search(_SAFE_FORMAT.sub("", text)))
    )


def information_question(text: str) -> str:
    clauses = [part.strip() for part in _TURN_BOUNDARY.split(text)]

    return " ".join(
        part
        for part in clauses
        if part
        and not _untrusted_instruction(part, _META)
        and not _PRIVATE_PROMPT_REQUEST.search(part)
    )


_REFERENCE_BOUNDARY = re.compile(r"((?<=[.!?]) +|\r?\n)")
_REFERENCE_OVERRIDE = re.compile(
    r"\b(?:вместо|маркер\w*|ответ\w*|сообщени\w*|"
    r"системн\w*\s+(?:роль|промпт\w*|инструкц\w*)|формат\s+json)\b",
    re.I,
)


def reference_text(text: str) -> str:
    parts = _REFERENCE_BOUNDARY.split(text)

    return "".join(
        part + (parts[index + 1] if index + 1 < len(parts) else "")
        for index, part in enumerate(parts)
        if index % 2 == 0 and not _untrusted_instruction(part, _REFERENCE_OVERRIDE)
    ).strip()
