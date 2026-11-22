"""Собирает сценарии агента из адаптеров ввода-вывода."""

from dataclasses import replace
from functools import partial

from agent.application.audit import Verification, audit, verify_answer
from agent.application.context import Turn, resolve
from agent.application.dialogue import dialogue_reply
from agent.application.generation import (
    answer_from_search as generate_answer,
)
from agent.application.generation import (
    answer_with_source_ids,
)
from agent.application.memory import RECENT_TURNS, summarize_history
from agent.application.service import AnswerService
from agent.application.skills import select_skills
from agent.config import (
    PROMPTS_DIRECTORY,
    SKILLS_DIRECTORY,
    USER_INSTRUCTIONS,
    flag,
    load_settings,
    optional,
    positive_int,
    product_prompt,
    required,
    search_limit,
)
from agent.domain.chat_policy import WEB_POLICIES, needs_source_match
from agent.domain.conversation import conversational_reply, is_casual_reaction
from agent.domain.grounding import check_answer, is_truncated
from agent.domain.query import information_question, reference_text
from agent.domain.sources import SearchResult
from agent.providers.document_cache import cached_document
from agent.providers.firecrawl import read_document
from agent.providers.inference import ask, decide, decide_source_match
from agent.providers.query_rewrite import rewrite_query
from agent.providers.search import make_searcher
from agent.runtime import DEADLINE, REQUEST_ID, deadline, phase, request_id


def answer_from_search(
    question: str,
    results: list[SearchResult],
    *,
    model: str | None = None,
    reasoning_enabled: bool | None = None,
    response_format: dict | None = None,
    prompt_suffix: str = "",
    preserve_relevant_clause: bool = True,
    attachment_text: str = "",
    image_source: dict | None = None,
    settings: dict[str, str] | None = None,
) -> dict:
    settings = load_settings() if settings is None else settings

    return generate_answer(
        question,
        results,
        complete=partial(ask, settings=settings),
        system_prompt=lambda: product_prompt(
            " ".join(
                required(settings, name)
                for name in ("WEB_SYSTEM_PROMPT", "WEB_ANSWER_FORMAT_PROMPT")
            )
        ),
        select=partial(
            select_skills,
            root=SKILLS_DIRECTORY,
            overrides=PROMPTS_DIRECTORY / "skills",
            legacy=partial(optional, settings),
        ),
        source_limit=positive_int(settings, "WEB_SOURCE_MAX_CHARS"),
        model=model,
        reasoning_enabled=reasoning_enabled,
        response_format=response_format,
        prompt_suffix=prompt_suffix,
        preserve_relevant_clause=preserve_relevant_clause,
        attachment_text=attachment_text,
        image_source=image_source,
    )


def _verifier(settings: dict[str, str]) -> Verification | None:
    if not flag(settings, "AGENT_VERIFY_ANSWERS"):
        return None

    return partial(
        audit,
        prompt="\n\n".join(
            required(settings, name)
            for name in ("EVIDENCE_AUDIT_PROMPT", "EVIDENCE_REFERENCE_PROMPT")
        ),
        budget=positive_int(settings, "AGENT_AUDIT_MAX_TOKENS"),
        settings=settings,
    )


def _make_service(
    settings: dict[str, str],
    attachment_text: str = "",
    *,
    discovery: bool = False,
    chat_context: dict[str, object] | None = None,
    check_admission_rights: bool = False,
    web_policy: str = "",
) -> AnswerService:
    read = flag(settings, "WEB_RECOVERY_READ") and optional(settings, "FIRECRAWL_API_KEY")
    search = flag(settings, "WEB_RECOVERY_SEARCH")
    reader = partial(read_document, settings=settings)
    dsn = optional(settings, "AGENT_CACHE_DSN")

    cached = None

    if dsn:
        cache = partial(
            cached_document,
            dsn=dsn,
            ttl=positive_int(settings, "AGENT_DOCUMENT_TTL_SECONDS"),
            capacity=positive_int(settings, "AGENT_DOCUMENT_CACHE_CAPACITY"),
        )
        cached = partial(cache, read=None)
        reader = partial(cache, read=reader)

    discovery_prompt = required(settings, "DISCOVERY_ANSWER_PROMPT") if discovery else ""

    original = (chat_context or {}).get("user_question")
    source_check = (
        partial(decide_source_match, original, settings=settings, chat_context=chat_context)
        if isinstance(original, str) and needs_source_match(web_policy, original)
        else None
    )
    detailed_web = web_policy in {"admission", "event", "funding"} or source_check is not None

    return AnswerService(
        verify=_verifier(settings),
        original_question=original if isinstance(original, str) else "",
        decide_source_match=source_check,
        web_answer=partial(
            answer_with_source_ids,
            chat_context=chat_context,
            select_web_sources=partial(ask, settings=settings, timeout_seconds=10)
            if web_policy in {"event", "funding", "official_page"}
            else None,
            page_only=web_policy == "official_page",
            review_rights=partial(ask, settings=settings, timeout_seconds=12)
            if check_admission_rights
            else None,
            review_claims=partial(ask, settings=settings, timeout_seconds=8)
            if detailed_web and not check_admission_rights
            else None,
            read=(reader if read else cached) if detailed_web else None,
            complete=partial(ask, settings=settings),
            system_prompt=product_prompt(
                "\n\n".join(
                    text
                    for text in (
                        required(settings, "WEB_SOURCE_ANSWER_PROMPT"),
                        WEB_POLICIES.get(web_policy, ("", ""))[1],
                    )
                    if text
                )
            ),
            select=partial(
                select_skills,
                root=SKILLS_DIRECTORY,
                overrides=PROMPTS_DIRECTORY / "skills",
                legacy=partial(optional, settings),
            ),
            source_limit=positive_int(settings, "WEB_SOURCE_MAX_CHARS"),
        )
        if not attachment_text
        else None,
        search_provider=required(settings, "SEARCH_PROVIDER"),
        backup_provider=optional(settings, "WEB_RECOVERY_PROVIDER") or "",
        search=lambda question: make_searcher(settings=settings)(question),
        generate=partial(
            answer_from_search,
            attachment_text=attachment_text,
            settings=settings,
            response_format={"type": "json_object"},
            reasoning_enabled=False if discovery else None,
            prompt_suffix=discovery_prompt,
        ),
        regenerate=partial(
            answer_from_search,
            attachment_text=attachment_text,
            settings=settings,
            response_format={"type": "json_object"},
            reasoning_enabled=not discovery and flag(settings, "WEB_RECOVERY_REASONING"),
            prompt_suffix=discovery_prompt,
        ),
        repair=partial(
            answer_from_search,
            attachment_text=attachment_text,
            settings=settings,
            response_format={"type": "json_object"},
            reasoning_enabled=False,
            prompt_suffix=(
                f"{discovery_prompt}\n\n"
                "Сначала выбери один короткий непрерывный фрагмент с полезным фактом, "
                "который можешь скопировать дословно. Дай короткий ответ по доказательству. "
                "Сохраняй условия и ограничения. Если подтверждается только часть, "
                "верни partial, даже если знаешь только один полезный факт, и добавь один "
                "вопрос в follow_up. Если речь о поступлении после бакалавриата, не переноси "
                "экзамены и правила первого курса на следующий уровень. Верни только объект JSON."
            ),
        ),
        cached=cached,
        read=reader if read else None,
        backup_search=(
            partial(make_searcher, required(settings, "WEB_RECOVERY_PROVIDER"), settings=settings)
            if search
            else None
        ),
        rewrite=partial(rewrite_query, settings=settings) if search else None,
        source_limit=positive_int(settings, "WEB_SOURCE_MAX_CHARS"),
        search_limit=search_limit(settings),
        recovery_limit=positive_int(settings, "WEB_RECOVERY_MAX_NEW_SOURCES"),
    )


def answer_with_saved_search(
    question: str, results: list[SearchResult], *, attachment_text: str = ""
) -> dict:
    service = _make_service(load_settings(), reference_text(attachment_text))
    model_calls = []

    def observe(generate):
        def wrapped(question, sources, /, *, model=None):
            result = generate(question, sources, model=model)

            if result.get("generation") is not None:
                model_calls.append(result["generation"])

            return result

        return wrapped

    runner = replace(
        service,
        search=lambda _: list(results),
        web_answer=None,
        generate=observe(service.generate),
        repair=observe(service.repair) if service.repair else None,
        regenerate=observe(service.regenerate) if service.regenerate else None,
    )
    limit = positive_int(load_settings(), "AGENT_TURN_TIMEOUT_SECONDS")
    token = DEADLINE.set(deadline("", limit))

    try:
        result = runner.answer(question)
        return {**result, "generation_calls": model_calls}
    finally:
        DEADLINE.reset(token)


def answer_question(
    question: str,
    *,
    model: str | None = None,
    attachment_text: str = "",
    quoted_text: str = "",
    history: list[Turn] | None = None,
    image_source: dict | None = None,
    user_instructions: str = "",
) -> dict:
    settings = load_settings()
    trace_token = None
    time_token = None

    if not REQUEST_ID.get():
        trace_token = REQUEST_ID.set(request_id(""))

    if DEADLINE.get() is None:
        time_token = DEADLINE.set(
            deadline("", positive_int(settings, "AGENT_TURN_TIMEOUT_SECONDS"))
        )

    preferences_token = USER_INSTRUCTIONS.set(user_instructions)

    try:
        return _answer_turn(
            question, model, attachment_text, quoted_text, history or [], settings, image_source
        )
    finally:
        USER_INSTRUCTIONS.reset(preferences_token)

        if time_token is not None:
            DEADLINE.reset(time_token)

        if trace_token is not None:
            REQUEST_ID.reset(trace_token)


def _answer_turn(
    question: str,
    model: str | None,
    attachment_text: str,
    quoted_text: str,
    history: list[Turn],
    settings: dict[str, str],
    image_source: dict | None,
) -> dict:
    question = information_question(question)

    if not question:
        return {
            "status": "clarification",
            "answer": "Уточните, какую информацию о вузе вы ищете.",
            "citations": [],
        }

    if image_source is None and not (
        USER_INSTRUCTIONS.get() or attachment_text or quoted_text or is_casual_reaction(question)
    ):
        if reply := conversational_reply(question):
            status, text = reply

            with phase("resolve_context") as trace:
                trace["decision"] = "local_reply"

            return {"status": status, "answer": text, "citations": []}

    if image_source is not None:
        return _answer_image(question, image_source, model=model, settings=settings)

    attachment_text = "\n\n".join(
        safe for part in (quoted_text, attachment_text) if (safe := reference_text(part))
    )

    with phase("resolve_context") as trace:
        question, clarification = resolve(question, history)
        trace["decision"] = "clarification" if clarification else "ready"

    if clarification:
        return {"status": "clarification", "answer": clarification, "citations": []}

    memory = {}

    if len(history) > RECENT_TURNS and not is_casual_reaction(question):
        memory = summarize_history(
            history,
            complete=partial(ask, settings=settings, timeout_seconds=8),
            model=model,
        )

    reply = dialogue_reply(
        question,
        history,
        settings,
        attachment_text=attachment_text,
        memory=memory,
        decide=partial(decide, settings=settings),
        review_reply=lambda question, recent, draft: decide(
            question, recent, settings=settings, draft=draft
        ),
        complete=partial(ask, settings=settings),
        model=model,
    )

    if search_question := reply.get("search_question"):
        subject_context = reply.get("subject_context")
        recent_user_messages = (
            [subject_context]
            if subject_context
            else [
                text
                for turn in history[-RECENT_TURNS:]
                if turn["role"] == "user" and (text := reference_text(turn["text"]))
            ]
        )
        service = _make_service(
            settings,
            attachment_text,
            discovery=bool(reply.get("discovery")),
            check_admission_rights=bool(reply.get("check_admission_rights")),
            web_policy=reply.get("web_policy", "search"),
            chat_context={
                "user_question": question,
                "earlier_chat": memory,
                "recent_user_messages": recent_user_messages,
            },
        )

        if additional := reply.get("additional_query"):
            return service.answer(search_question, model=model, additional_query=additional)

        return service.answer(search_question, model=model)

    return reply


def _answer_image(question: str, image_source: dict, *, model: str | None, settings: dict) -> dict:
    text = reference_text(image_source["text"])

    if text:
        result = answer_from_search(
            question,
            [],
            model=model,
            image_source={**image_source, "text": text},
            settings=settings,
            response_format={"type": "json_object"},
        )

        if result["status"] != "invalid" and not is_truncated(result):
            return verify_answer(_verifier(settings), question, check_answer(question, result))

    return {
        "status": "unverified",
        "answer": "По распознанному тексту фотографии не удалось подтвердить ответ.",
        "citations": [],
    }


def recover_answer(
    question: str,
    results: list[SearchResult],
    initial: dict,
    *,
    model: str | None = None,
    settings: dict[str, str] | None = None,
) -> dict:
    service = _make_service(load_settings() if settings is None else settings)
    return service.recover(question, results, initial, model=model)
