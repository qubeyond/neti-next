"""Правила выбора документа и границ доказательства."""

import re
from ipaddress import ip_address
from urllib.parse import parse_qsl, urlsplit

from .passages import QUERY_STOP_WORDS, WORD
from .sources import SearchResult

PROGRAM_CODE = re.compile(r"\b\d{2}\.\d{2}\.\d{2}\b")
NAMED_DOCUMENT = re.compile(r'[«"]([^»"]{6,})[»"]')
ACRONYM = re.compile(r"\b[А-ЯЁ][А-ЯЁа-яё]{2,}\b")
QUESTION_WORDS = QUERY_STOP_WORDS | {
    "где",
    "как",
    "какую",
    "каков",
    "каково",
    "мне",
    "я",
}


def topic_terms(text: str) -> set[str]:
    return {
        word[:5]
        for word in WORD.findall(text.lower())
        if len(word) >= 4 and word not in QUESTION_WORDS
    }


def read_candidate(question: str, sources: list[SearchResult]) -> int | None:
    codes = set(PROGRAM_CODE.findall(question))
    years = set(re.findall(r"\b20\d{2}\b", question))
    names = [" ".join(name.lower().split()) for name in NAMED_DOCUMENT.findall(question)]
    acronyms = {
        word.lower()
        for word in ACRONYM.findall(question)
        if sum(letter.isupper() for letter in word) >= 3
    }
    topics = topic_terms(question) - {word[:5] for word in acronyms}
    ranked = []

    for index, source in enumerate(sources):
        title = source["title"]
        snippet = source["snippet"]

        if not document_key(source["url"]):
            continue

        words = WORD.findall(title.lower())
        institution_name_initials = {
            "".join(word[0] for word in words[i : i + len(short)])
            for short in acronyms
            for i in range(len(words) - len(short) + 1)
        }
        title_match = topics & topic_terms(title)
        evidence_match = topics & topic_terms(snippet)
        named = any(name in " ".join(title.lower().split()) for name in names)
        code = bool(codes & set(PROGRAM_CODE.findall(snippet)))
        dated_file = code and any(year in urlsplit(source["url"]).path for year in years)
        institution = acronyms & (set(words) | institution_name_initials)
        on_topic = bool(
            institution and title_match and len(evidence_match) >= max(2, len(topics) - 2)
        )

        if named or code or on_topic:
            score = (
                named,
                dated_file,
                code,
                0 if code else len(title_match),
                0 if code else len(evidence_match),
            )

            ranked.append((score, index))

    return max(ranked, key=lambda pair: pair[0])[1] if ranked else None


def document_key(url: str) -> tuple | None:
    if not isinstance(url, str):
        return None

    try:
        parsed = urlsplit(url)
        host = parsed.hostname

        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or "." not in host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
            or host.endswith((".local", ".internal", ".localhost"))
        ):
            return None

        try:
            address = ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None

        return (
            host.removeprefix("www."),
            parsed.path.rstrip("/"),
            tuple(sorted((k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_"))),
        )
    except ValueError:
        return None


def document_excerpt(text: str, question: str, limit: int) -> str:
    codes = set(PROGRAM_CODE.findall(question))

    if codes:
        program_records = re.split(r"(?m)(?=^[ \t]*(?:\|\s*)?\d{2}\.\d{2}\.\d{2}\b)", text)
        matches = [
            group.strip()
            for group in program_records
            if set(PROGRAM_CODE.findall(group.lstrip().lstrip("|").lstrip()[:8])) & codes
        ]

        if matches:
            return "\n".join(matches)[:limit]

    terms = topic_terms(question)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    best = ""
    best_score = (0, 0)

    for start, first in enumerate(lines):
        block = first[:limit]

        for line in lines[start + 1 : start + 12]:
            if len(block) + 1 + len(line) > limit:
                break

            block += "\n" + line

        score = (len(terms & topic_terms(block)), len(terms & topic_terms(first)))

        if score > best_score:
            best, best_score = block, score

    return best
