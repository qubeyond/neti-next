import json
import re
from html import unescape
from unicodedata import normalize

from .baseline import ask
from .config import _local_settings, _positive_int, _setting


def json_object(text: str) -> dict:
    text = re.sub(
        r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.IGNORECASE
    )
    value = json.loads(text)
    if not isinstance(value, dict):
        raise TypeError("Expected JSON object")
    return value


def _plain(text: str) -> str:
    return " ".join(
        normalize("NFKC", unescape(re.sub(r"</?\w+[^>]*>", "", text))).split()
    )


def prepare_sources(results: list[dict], limit: int) -> list[dict]:
    return [
        {
            "source": index,
            "url": item["url"],
            "title": _plain(item["title"]),
            "text": _plain(item["snippet"])[:limit],
        }
        for index, item in enumerate(results, start=1)
        if _plain(item["snippet"])
    ]


def parse_answer(text: str, sources: list[dict]) -> dict:
    try:
        value = json_object(text)
        status, answer, citations = value["status"], value["answer"], value["citations"]
        if status not in ("answered", "partial", "unverified"):
            raise ValueError
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or not isinstance(citations, list)
        ):
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
    for citation in citations:
        if (
            not isinstance(citation, dict)
            or type(citation.get("source")) is not int
            or not isinstance(citation.get("quote"), str)
        ):
            malformed += 1
            continue
        source = by_number.get(citation["source"])
        quote = _plain(citation["quote"])
        checked.append(
            {
                "source": citation["source"],
                "url": source["url"] if source else None,
                "quote": quote,
                "verified": bool(source and quote and quote in source["text"]),
            }
        )
    return {
        "status": status,
        "answer": answer,
        "citations": checked,
        "malformed_citations": malformed,
    }


def answer_from_search(
    question: str, results: list[dict], *, model: str | None = None
) -> dict:
    settings = _local_settings()
    sources = prepare_sources(results, _positive_int(settings, "WEB_SOURCE_MAX_CHARS"))
    if not sources:
        return {
            "status": "unverified",
            "answer": "Не нашёл фрагментов, подтверждающих ответ.",
            "citations": [],
            "malformed_citations": 0,
            "sources": [],
            "generation": None,
        }
    prompt = json.dumps(
        {"question": question, "search_fragments": sources}, ensure_ascii=False
    )
    generation = ask(
        prompt,
        model=model,
        system_prompt=_setting(settings, "WEB_SYSTEM_PROMPT"),
        allow_empty=True,
    )
    return {
        **parse_answer(generation["answer"], sources),
        "sources": sources,
        "generation": generation,
    }
