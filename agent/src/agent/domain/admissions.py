"""Извлекает общее условие поступления из официальных правил вуза."""

import re

from agent.domain.evidence import prepare_sources
from agent.domain.recovery import document_key
from agent.domain.sources import SearchResult

_GRADUATE = re.compile(r"\b(?:магистратур\w*|после\b.{0,35}\bбакалавр\w*)", re.I)
_ADMISSION = re.compile(r"\bпоступ\w*", re.I)
_TARGET = re.compile(r"\bв\s+([a-z]{2,10})\b", re.I)
_GENERAL_RULES = re.compile(
    r"\b(?:graduate[-/ ]+(?:education|admissions)|admissions[-/ ]+graduate)\b", re.I
)
_BACHELOR_REQUIREMENT = re.compile(
    r"\b(?:an applicant|applicants) (?:must|are required to) "
    r"(?:normally )?(?:have received|have earned|have|hold) (?:a )?"
    r"bachelor['’]s degree or (?:its |an )?equivalent\b",
    re.I,
)


def graduate_admission_fact(question: str, results: list[SearchResult]) -> dict | None:
    target = _TARGET.search(question)

    if target is None or not (_GRADUATE.search(question) and _ADMISSION.search(question)):
        return None

    university = target[1]

    for source in prepare_sources(results, 4000):
        key = document_key(source["url"])

        if (
            not key
            or not key[0].endswith(".edu")
            or university.lower() not in key[0].split(".")[:-1]
        ):
            continue

        if not _GENERAL_RULES.search(f"{source['title']} {source['url']}"):
            continue

        if quote := _BACHELOR_REQUIREMENT.search(source["text"]):
            return {
                "status": "partial",
                "answer": (
                    f"Если хочешь поступить в {university.upper()} после бакалавриата, "
                    "начни с выбора программы. По общим правилам приёма на программы "
                    "после бакалавриата обычно нужен диплом бакалавра или его эквивалент〔1〕. "
                    "Подойдёт ли твой диплом и какие документы понадобятся, "
                    "нужно сверить с условиями выбранной программы."
                ),
                "follow_up": "Ты хочешь получить степень или рассматриваешь онлайн-курсы?",
                "citations": [
                    {
                        "source": source["source"],
                        "url": source["url"],
                        "quote": quote[0],
                        "ref": 1,
                        "verified": True,
                    }
                ],
                "sources": [source],
                "malformed_citations": 0,
            }

    return None
