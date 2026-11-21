"""Переформулирует поисковый запрос через RouterAI."""

import json
import re

from agent.config import flag, load_settings, positive_int, required
from agent.domain.errors import AgentError
from agent.domain.evidence import json_object
from agent.domain.recovery import QUESTION_WORDS
from agent.domain.sources import SearchResult
from agent.providers.inference import ask

QUERY_NUMBER = re.compile(r"\b\d+(?:[./]\d+)*\b")


def rewrite_query(
    question: str, sources: list[SearchResult], *, settings: dict[str, str] | None = None
) -> tuple[str, dict | None]:
    settings = load_settings() if settings is None else settings
    title_limit = positive_int(settings, "WEB_REWRITE_MAX_TITLE_CHARS")
    query_limit = positive_int(settings, "WEB_REWRITE_MAX_QUERY_CHARS")
    prompt = required(settings, "WEB_REWRITE_PROMPT")
    budget = positive_int(settings, "WEB_REWRITE_MAX_TOKENS")
    reasoning = flag(settings, "WEB_REWRITE_REASONING")
    fallback = " ".join(
        word for word in question.split() if word.lower().strip(",.?!:") not in QUESTION_WORDS
    )
    call = None

    try:
        call = ask(
            json.dumps(
                {
                    "question": question,
                    "candidate_titles": [row["title"][:title_limit] for row in sources],
                },
                ensure_ascii=False,
            ),
            system_prompt=prompt,
            max_tokens=budget,
            reasoning_enabled=reasoning,
            allow_empty=True,
            settings=settings,
        )
        query = json_object(call["answer"]).get("query")

        if (
            isinstance(query, str)
            and 0 < len(query.strip()) <= query_limit
            and set(QUERY_NUMBER.findall(question)) <= set(QUERY_NUMBER.findall(query))
        ):
            return " ".join(query.split()), call
    except (AgentError, ValueError, TypeError):
        pass

    return fallback or question, call
