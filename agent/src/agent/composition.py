"""Собирает сценарии агента из адаптеров ввода-вывода."""

import os
from functools import partial

from .modules.answers.application.generation import (
    answer_from_search as generate_answer,
)
from .modules.answers.application.service import AnswerService
from .modules.answers.domain.sources import SearchResult
from .modules.answers.infrastructure.firecrawl import read_document
from .modules.answers.infrastructure.query_rewrite import rewrite_query
from .modules.answers.infrastructure.routerai import ask
from .modules.answers.infrastructure.search import make_searcher
from .shared.config import _local_settings, _positive_int, _setting


def answer_from_search(
    question: str,
    results: list[SearchResult],
    *,
    model: str | None = None,
    reasoning_enabled: bool | None = None,
    response_format: dict | None = None,
    prompt_suffix: str = "",
    preserve_relevant_clause: bool = True,
) -> dict:
    settings = _local_settings()

    return generate_answer(
        question,
        results,
        complete=ask,
        system_prompt=lambda: _setting(settings, "WEB_SYSTEM_PROMPT"),
        source_limit=_positive_int(settings, "WEB_SOURCE_MAX_CHARS"),
        model=model,
        reasoning_enabled=reasoning_enabled,
        response_format=response_format,
        prompt_suffix=prompt_suffix,
        preserve_relevant_clause=preserve_relevant_clause,
    )


def _enabled(settings: dict[str, str], name: str) -> bool:
    return os.environ.get(name, settings.get(name, "false")).lower() == "true"


def _make_service(settings: dict[str, str]) -> AnswerService:
    read = _enabled(settings, "WEB_RECOVERY_READ")
    search = _enabled(settings, "WEB_RECOVERY_SEARCH")

    return AnswerService(
        search=lambda question: make_searcher()(question),
        generate=answer_from_search,
        read=partial(read_document, settings=settings) if read else None,
        backup_search=partial(make_searcher, "tavily") if search else None,
        rewrite=rewrite_query if search else None,
        source_limit=_positive_int(settings, "WEB_SOURCE_MAX_CHARS"),
        search_limit=_positive_int(settings, "SEARCH_MAX_RESULTS") if search else 5,
    )


def answer_question(question: str, *, model: str | None = None) -> dict:
    return _make_service(_local_settings()).answer(question, model=model)


def recover_answer(
    question: str,
    results: list[SearchResult],
    initial: dict,
    *,
    model: str | None = None,
    settings: dict[str, str] | None = None,
) -> dict:
    service = _make_service(_local_settings() if settings is None else settings)
    return service.recover(question, results, initial, model=model)
