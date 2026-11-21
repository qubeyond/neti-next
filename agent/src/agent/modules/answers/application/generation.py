"""Готовит контекст и формирует ответ по найденным источникам."""

import json
from collections.abc import Callable

from ..domain.evidence import parse_answer, prepare_sources
from ..domain.sources import SearchResult
from .ports import Completion


def answer_from_search(
    question: str,
    results: list[SearchResult],
    *,
    complete: Completion,
    system_prompt: Callable[[], str],
    source_limit: int,
    model: str | None = None,
    reasoning_enabled: bool | None = None,
    response_format: dict | None = None,
    prompt_suffix: str = "",
    preserve_relevant_clause: bool = True,
) -> dict:
    sources = prepare_sources(
        results,
        source_limit,
        question=question if preserve_relevant_clause else "",
    )

    if not sources:
        return {
            "status": "unverified",
            "answer": "Не нашёл фрагментов, подтверждающих ответ.",
            "citations": [],
            "malformed_citations": 0,
            "sources": [],
            "generation": None,
        }

    prompt = json.dumps({"question": question, "search_fragments": sources}, ensure_ascii=False)
    generation = complete(
        prompt,
        model=model,
        system_prompt=system_prompt() + prompt_suffix,
        allow_empty=True,
        reasoning_enabled=reasoning_enabled,
        response_format=response_format,
    )

    return {
        **parse_answer(generation["answer"], sources),
        "sources": sources,
        "generation": generation,
    }
