"""Обрабатывает реплики, для которых не нужны внешние источники."""

import re

_GREETING = r"(?:привет(?:ик)?|здравствуй(?:те)?|доброе утро|добрый (?:день|вечер))"
_CAPABILITIES = (
    r"(?:кто ты(?: такой)?|ты (?:вообще )?кто|(?:а )?(?:что|че) (?:ты )?(?:умеешь|можешь)"
    r"|(?:а )?чем (?:ты )?можешь помочь|чем ты занимаешься|расскажи о себе|ты (?:бот|нейросеть))"
)
_CAPABILITY_LIST = re.compile(
    r"(?:перечисли|назови|расскажи) (?:все,? )?(?:что (?:ты )?умеешь|"
    r"(?:свои )?возможности)"
)
_INTRODUCTION = re.compile(rf"(?:{_GREETING}(?:[ ,.!?]+{_CAPABILITIES})?|{_CAPABILITIES})")
_HOW_ARE_YOU = re.compile(r"(?:ну\s+)?(?:как (?:твои )?дела|как (?:ты|сам|поживаешь))")
_THANKS = re.compile(r"(?:спасибо(?: большое)?|большое спасибо|благодарю|спс|пасиб(?:о)?)")
_ACKNOWLEDGMENT = re.compile(r"(?:понятно|понял[аи]?|ясн(?:о)?|ок(?:ей)?|хорошо)")
_REACTION = re.compile(
    r"(?:ну\s+)?(?:чел|бро|братан|мэн|лол|кек|рофл|(?:а?ха){2,}|ору|угар|жесть|капец|"
    r"блин|бля(?:ть|дь)?|пиздец|господи|офигеть|ничего себе|да ладно|да ну|в смысле|серьезно|"
    r"ты (?:серьезно|прикалываешься|шутишь|даешь|че|чо)|красава|пон|норм|имба)"
    r"(?:(?:\s+|\s*[,.!?]+\s*)(?:ну\s+)?(?:чел|бро|да ладно|серьезно|ну))?"
)
_REACTION_EMOJI = re.compile(r"[😂🤣😅😆🙃🤔😳😬😐😮🫠🙄😭🤦👍👀🤨❤️\s!?.,…]+")
_PRIVATE_ACADEMIC = re.compile(
    r"\b(?:моя оценка|мои оценки|мо[ию] оценк[иу]|мой средний балл|"
    r"расписание лично для меня|статус моего заявления|рассмотрели ли мое заявление)\b"
    r"|\b(?:какую|какая) оценку?\b.*\bмне\b"
    r"|\b(?:какую оценку|сколько баллов)\b.*\bя (?:получил[аи]?|набрал[аи]?)\b"
)
_INTRODUCTION_REPLY = (
    "Привет! Я Пигеон. Ищу информацию о вузах Новосибирска и других городов — "
    "от правил поступления до программ и общежитий."
)


def is_casual_reaction(question: str) -> bool:
    text = " ".join(question.casefold().replace("ё", "е").split()).strip("!?.,…:;\\/—–- ")
    text = re.sub(r"([а-я])\1+", r"\1", text)

    return bool(_REACTION.fullmatch(text) or _REACTION_EMOJI.fullmatch(text))


def conversational_reply(question: str) -> tuple[str, str] | None:
    text = " ".join(question.casefold().replace("ё", "е").split()).strip("!?.,…:;\\/—–- ")

    if sum(character.isalnum() for character in text) <= 1 or re.fullmatch(
        r"(?:э+|эм+|мм+|хм+|ну+)", text
    ):
        return "clarification", "Что ты хотел спросить?"

    if _CAPABILITY_LIST.fullmatch(text):
        return "answered", "Я Пигеон. Ищу информацию о вузах Новосибирска и других городов."

    if _INTRODUCTION.fullmatch(text):
        reply = _INTRODUCTION_REPLY

        if re.fullmatch(r"(?:кто ты(?: такой)?|ты (?:вообще )?кто|ты (?:бот|нейросеть))", text):
            reply = "Я Пигеон, помощник по вузам Новосибирска и других городов."

        return "answered", reply

    if _HOW_ARE_YOU.fullmatch(text):
        return "answered", "Всё спокойно. А у тебя?"

    if _THANKS.fullmatch(text):
        return "answered", "Пожалуйста!"

    if _ACKNOWLEDGMENT.fullmatch(text):
        return "answered", "Понял."

    if _PRIVATE_ACADEMIC.search(text):
        return (
            "answered",
            "Я вижу только открытые материалы. Персональные оценки и статус заявления "
            "проверьте в личном кабинете вуза. Могу подсказать, где его найти.",
        )

    return None
