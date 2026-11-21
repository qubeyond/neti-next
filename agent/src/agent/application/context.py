"""Уточняет недостающие условия текущего вопроса."""

import json
import re
from typing import Literal, TypedDict

from agent.domain.errors import AgentError
from agent.domain.evidence import json_object
from agent.providers.inference import ask

_CONTEXTUAL = re.compile(
    r"^(?:а\b|и\b|да\b|нет\b|там\b|тогда\b|если\b|что если\b|"
    r"(?:сколько|где|когда|какая?|какие|какой)\s+(?:это|там|она|он|оно|они)\b)",
    re.IGNORECASE,
)
_EXPLICIT_REFERENCE = re.compile(
    r"\b(?:этот|этого|этой|этом|тот же|там же|то же самое|"
    r"предыдущ\w*\s+(?:вопрос\w*|ответ\w*))\b",
    re.I,
)
_AMBIGUOUS = re.compile(
    r"^(?:где (?:взять|получить) справку|сколько стоит (?:общежитие|общага|обучение)"
    r"|где расписание(?: моей группы| занятий)?|когда подавать документы)[?!.\s]*$",
    re.I,
)
_INSTITUTION_ABBREVIATION = re.compile(r"^[А-ЯЁA-Z]{2,12}(?: [А-ЯЁA-Z]{2,12}){0,2}$")
_INSTITUTION_CLARIFICATION = re.compile(r"\b(?:каком вузе|какой вуз|какой университет)\b", re.I)

_IDENTIFIERS = re.compile(r"\b(?:20\d{2}|\d{2}\.\d{2}\.\d{2})\b")
_MISSING_REFERENT = re.compile(r"\b(?:туда|в этот вуз|в тот вуз|на эту программу)\b", re.I)
_TIME_SCOPE = re.compile(
    r"\b(?:20\d{2}|бакалавр\w*|специалитет\w*|магистрат\w*|аспирант\w*|спо|колледж\w*|егэ|внутренн\w*)\b",
    re.I,
)
_DEADLINE_REQUEST = re.compile(r"\bдо какого\b.*\bдокумент\w*\b", re.I)
_LOCAL_SERVICE = re.compile(r"\b(?:справк\w*|общежит\w*|общаг\w*)\b", re.I)
_NAMED_PLACE = re.compile(
    r"\b[А-ЯЁ]{3,}\b|\b(?:[А-ЯЁа-яё-]+\s+(?i:университет\w*|институт\w*|академи\w*)"
    r"|(?i:новосибирск\w*|все вузы|по вузам))\b"
)


def _clarify(question: str) -> str:
    if _DEADLINE_REQUEST.search(question) and not _TIME_SCOPE.search(question):
        return "Уточните уровень обучения и год приёма."

    if (
        _AMBIGUOUS.fullmatch(question)
        or _MISSING_REFERENT.search(question)
        or (_LOCAL_SERVICE.search(question) and not _NAMED_PLACE.search(question))
    ):
        return "Уточните, пожалуйста, о каком вузе идёт речь."

    return ""


class Turn(TypedDict):
    role: Literal["user", "assistant"]
    text: str


def resolve(question: str, history: list[Turn], *, prompt: str, budget: int) -> tuple[str, str]:
    wants_context = _CONTEXTUAL.search(question) or _EXPLICIT_REFERENCE.search(question)

    if not history:
        return question, _clarify(question)

    if (
        len(history) >= 2
        and history[-1]["role"] == "assistant"
        and history[-2]["role"] == "user"
        and _INSTITUTION_ABBREVIATION.fullmatch(question.strip())
        and _INSTITUTION_CLARIFICATION.search(history[-1]["text"])
    ):
        previous = history[-2]["text"].rstrip(" ?.!")

        return f"{previous} в вузе {question.strip()}?", ""

    if not wants_context and len(question.split()) > 3 and not _clarify(question):
        return question, ""

    try:
        response = ask(
            json.dumps({"question": question, "history": history}, ensure_ascii=False),
            system_prompt=prompt,
            response_format={"type": "json_object"},
            reasoning_enabled=False,
            max_tokens=budget,
            allow_empty=True,
        )
        decision = json_object(response["answer"])
        standalone = decision.get("question")
        clarification = decision.get("clarification")

        if (
            isinstance(standalone, str)
            and standalone.strip()
            and len(standalone) <= 2000
            and set(_IDENTIFIERS.findall(question)) <= set(_IDENTIFIERS.findall(standalone))
        ):
            return standalone.strip(), ""

        if isinstance(clarification, str) and 0 < len(clarification.strip()) <= 300:
            return question, clarification.strip()
    except (AgentError, ValueError, TypeError):
        pass

    return question, ""
