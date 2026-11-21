"""Готовит контекст и формирует ответ по найденным источникам."""

import json
from collections.abc import Callable

from agent.application.skills import ActiveSkills
from agent.domain.evidence import parse_answer, prepare_sources
from agent.domain.ports import Completion
from agent.domain.sources import SearchResult
from agent.runtime import phase


def answer_from_search(
    question: str,
    results: list[SearchResult],
    *,
    complete: Completion,
    system_prompt: Callable[[], str],
    source_limit: int,
    select: Callable[..., ActiveSkills],
    model: str | None = None,
    reasoning_enabled: bool | None = None,
    response_format: dict | None = None,
    prompt_suffix: str = "",
    preserve_relevant_clause: bool = True,
    attachment_text: str = "",
    image_source: dict | None = None,
) -> dict:
    sources = prepare_sources(
        results,
        source_limit,
        question=question if preserve_relevant_clause else "",
    )

    if image_source is not None:
        sources = [
            {
                "source": 1,
                "url": "",
                "title": "Фото пользователя",
                "text": image_source["text"],
                "attachment_id": image_source["id"],
            }
        ]

    if not sources:
        return {
            "status": "unverified",
            "answer": "Не нашёл фрагментов, подтверждающих ответ.",
            "citations": [],
            "malformed_citations": 0,
            "sources": [],
            "generation": None,
        }

    context = {"question": question, "search_fragments": sources}

    if attachment_text:
        context["attachment_text"] = attachment_text

    prompt = json.dumps(context, ensure_ascii=False)

    with phase("select_skills") as trace:
        active = select(
            question,
            attachment=bool(attachment_text or image_source),
            photo_only=image_source is not None,
            table=any(item["text"].count("|") >= 4 for item in sources),
        )
        trace["active_skills"] = list(active.ids)

    instructions = "\n\n".join(
        text for text in (system_prompt(), *active.instructions, prompt_suffix) if text
    )
    generation = complete(
        prompt,
        model=model,
        system_prompt=instructions,
        allow_empty=True,
        reasoning_enabled=reasoning_enabled,
        response_format=response_format,
    )

    answer = parse_answer(generation["answer"], sources)

    if "blocks" in answer:
        answer["blocks"] = [block for block in answer["blocks"] if block["kind"] in active.blocks]

    if not active.events:
        answer.pop("events", None)

    return {
        **answer,
        "active_skills": list(active.ids),
        "sources": sources,
        "generation": generation,
    }
