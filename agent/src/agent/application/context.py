"""Уточняет недостающие условия текущего вопроса."""

import re
from typing import Literal, TypedDict

_AMBIGUOUS = re.compile(
    r"^(?:где (?:взять|получить) справку|сколько стоит (?:общежитие|общага|обучение)"
    r"|где расписание(?: моей группы| занятий)?|когда подавать документы)[?!.\s]*$",
    re.I,
)
_INSTITUTION_ABBREVIATION = re.compile(r"^[А-ЯЁA-Z]{2,12}(?: [А-ЯЁA-Z]{2,12}){0,2}$")
_INSTITUTION_CLARIFICATION = re.compile(r"\b(?:каком вузе|какой вуз|какой университет)\b", re.I)
_TERM_CLARIFICATION = re.compile(
    r'\bчто\s+(?:означает|значит|имеется в виду под)\s*[«"]([^»"\n]{1,50})[»"]',
    re.I,
)

_UNIVERSITY_SLANG = re.compile(r"\bуник(?:и(?:к)?|ов|ам|ах|ами)?\b", re.I)
_MIT_ALIAS = re.compile(r"\b(?:MIT|МИТ)\b", re.I)
_MIT_SHORT = re.compile(r"\s*(?:(?:а|и)\s+)?(?:MIT|МИТ)[!?.,\s]*", re.I)
_MIT_NAME = "Массачусетский технологический институт (MIT)"
_DATA_SCIENCE_REPLY = re.compile(r"(?:дс|ds|data science)[?!.\s]*", re.I)
_DATA_SCIENCE_OPTION = re.compile(r"\b(?:data science|дата сайенс|наука о данных)\b", re.I)
_PICK_PROGRAM = re.compile(r"\b(?:найди|подбери|посоветуй|поищи|ищу|подбираю)\b", re.I)

_OVERVIEW = re.compile(r"^(?:расскажи(?:те)?|что знаешь)\s+(?:о|об|про)\s+", re.I)
_OVERVIEW_SWITCH = re.compile(
    r"^(?:[аАиИ] )?([А-ЯЁA-Z]{2,12}|(?i:мгу|мфти|нгу|нгту|нгпу|вшэ|спбгу|сгугит|сгупс))"
    r"[?!.\s]*$"
)
_FULL_NAME = re.compile(
    r"^(?:а )?(?:как (?:полностью называется|называется)(?: вуз)?(?: полностью)?"
    r"|какое (?:у него )?полное (?:название|наименование)(?: вуза)?)\s*[?!.]*$",
    re.I,
)
_ABBREVIATIONS = re.compile(r"\b[А-ЯЁA-Z]{2,12}\b")

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


def resolve(question: str, history: list[Turn]) -> tuple[str, str]:
    mit_alias = _MIT_SHORT.fullmatch(question)

    if history and history[-1]["role"] == "assistant":
        last_reply = history[-1]["text"]
        short_name = mit_alias or _INSTITUTION_ABBREVIATION.fullmatch(question.strip())

        if short_name and _INSTITUTION_CLARIFICATION.search(last_reply):
            if term := _TERM_CLARIFICATION.search(last_reply):
                university = "MIT" if mit_alias else question.strip()

                return (
                    question,
                    f"Понял, речь про {university}. Что ты имеешь в виду под «{term[1]}»?",
                )

    if mit_alias:
        if (
            len(history) >= 2
            and history[-1]["role"] == "assistant"
            and _INSTITUTION_CLARIFICATION.search(history[-1]["text"])
        ):
            previous = history[-2]["text"]

            if history[-2]["role"] == "user" and _clarify(previous):
                return f"{previous.rstrip(' ?.!')} в вузе {_MIT_NAME}?", ""

        return f"Расскажи кратко про университет {_MIT_NAME} по официальным сведениям.", ""

    if not re.search(r"массачусетск|massachusetts", question, re.I):
        question = _MIT_ALIAS.sub(_MIT_NAME, question)

    question = _UNIVERSITY_SLANG.sub("университеты", question)

    overview = _OVERVIEW.match(question)

    if overview and (named := _OVERVIEW_SWITCH.fullmatch(question[overview.end() :].strip())):
        university = named[1].upper()

        return f"Расскажи кратко про университет {university} по официальным сведениям.", ""

    if not history:
        return question, _clarify(question)

    if (
        len(history) >= 2
        and history[-1]["role"] == "assistant"
        and history[-2]["role"] == "user"
        and _DATA_SCIENCE_REPLY.fullmatch(question)
        and _DATA_SCIENCE_OPTION.search(history[-1]["text"])
        and _PICK_PROGRAM.search(history[-2]["text"])
    ):
        return "Подбери учебные программы по Data Science", ""

    if (
        len(history) >= 2
        and history[-1]["role"] == "assistant"
        and history[-2]["role"] == "user"
        and _INSTITUTION_ABBREVIATION.fullmatch(question.strip())
        and _INSTITUTION_CLARIFICATION.search(history[-1]["text"])
    ):
        previous = history[-2]["text"].rstrip(" ?.!")

        return f"{previous} в вузе {question.strip()}?", ""

    previous = next((turn["text"] for turn in reversed(history) if turn["role"] == "user"), "")

    if _OVERVIEW.match(previous) and (switch := _OVERVIEW_SWITCH.fullmatch(question.strip())):
        university = switch[1].upper()

        return f"Расскажи кратко про университет {university} по официальным сведениям.", ""

    if _FULL_NAME.fullmatch(question.strip()):
        names = set(_ABBREVIATIONS.findall(previous)) - {"ЕГЭ", "ОГЭ", "РФ", "ИТ"}

        if len(names) == 1:
            return f"Каково полное официальное наименование вуза {names.pop()}?", ""

    return question, ""
