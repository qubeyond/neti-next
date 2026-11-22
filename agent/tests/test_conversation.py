"""Проверяет знакомство с Пигеоном и переход к фактическому вопросу."""

import json

import pytest
from fastapi.testclient import TestClient

from agent import composition
from agent.app import app
from agent.application import context
from agent.application.dialogue import dialogue_reply
from agent.application.service import AnswerService
from agent.domain.conversation import conversational_reply
from agent.domain.grounding import check_answer
from agent.domain.institution import institution_fact
from agent.domain.sources import SearchResult, evidence_sources


@pytest.mark.parametrize(
    "question", ["привет че умеешь", "Здравствуйте!", "Кто ты?", "Чем можешь помочь?"]
)
def test_product_introduction_has_no_sources_or_provider_calls(monkeypatch, question):
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")
    monkeypatch.setenv("AGENT_TOKEN", "conversation-test")

    def unexpected(*_args, **_kwargs):
        pytest.fail("Знакомство отправлено на внешний поиск или генерацию.")

    monkeypatch.setattr(composition, "_make_service", unexpected)

    response = TestClient(app).post(
        "/agent/v1/answers",
        json={"question": question, "history": [{"role": "user", "text": "НГУ"}]},
        headers={"Authorization": "Bearer conversation-test"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "answered"
    assert "Пигеон" in response.json()["answer"]
    assert "вузам Новосибирска" in response.json()["answer"]
    assert response.json()["citations"] == []


def _web_call(question):
    return {
        "answer": "",
        "finish_reason": "tool_calls",
        "tool_calls": [
            {
                "type": "function",
                "function": {"name": "web_search", "arguments": json.dumps({"question": question})},
            }
        ],
    }


def test_greeting_in_factual_question_still_searches_and_skips_shared_chat(monkeypatch):
    shared: SearchResult = {
        "url": "https://www.chat01.ai/en/chat/demo",
        "title": "Чужой ответ",
        "snippet": "Текст",
    }
    official: SearchResult = {
        "url": "https://www.nsu.ru/about",
        "title": "НГУ",
        "snippet": "Ул. Пирогова, 2.",
    }

    def search(question):
        assert "где находится НГУ" in question
        return [shared, official]

    def generate(question, results, **_options):
        assert "где находится НГУ" in question
        assert results == [official]

        return {
            "status": "answered",
            "answer": "Ул. Пирогова, 2.",
            "citations": [{"url": official["url"], "quote": official["snippet"], "verified": True}],
            "malformed_citations": 0,
        }

    monkeypatch.setattr(
        composition,
        "_make_service",
        lambda *_args: AnswerService(
            search=search, generate=generate, source_limit=1000, search_limit=5, recovery_limit=1
        ),
    )

    monkeypatch.setattr(
        composition, "ask", lambda *_args, **_kwargs: _web_call("где находится НГУ?")
    )

    result = composition.answer_question("Привет, где находится НГУ?")

    assert "Пирогова" in result["answer"]


def test_custom_web_prompt_retains_product_role(monkeypatch):
    monkeypatch.setenv("WEB_SYSTEM_PROMPT", "Локальные правила ответа")

    def complete(_prompt, *, system_prompt, **_options):
        assert system_prompt.startswith(
            "Ты Пигеон, помощник по вузам Новосибирска и других городов."
        )
        assert "Локальные правила ответа" in system_prompt

        return {"answer": '{"status":"answered","answer":"Проверка","citations":[]}'}

    monkeypatch.setattr(composition, "ask", complete)

    result = composition.answer_from_search(
        "Где находится НГУ?",
        [{"url": "https://www.nsu.ru/about", "title": "НГУ", "snippet": "Ул. Пирогова, 2."}],
    )

    assert result["answer"] == "Проверка"


@pytest.mark.parametrize(
    ("question", "status", "fragment"),
    [
        ("привет ты кто\\", "answered", "Я Пигеон"),
        ("спасибо!!", "answered", "Пожалуйста"),
        ("???", "clarification", "Что ты хотел спросить"),
        ("э", "clarification", "Что ты хотел спросить"),
        ("какую оценку мне вчера поставили по физике в НГУ?", "answered", "личном кабинете"),
    ],
)
def test_conversational_reply_routes_without_search(question, status, fragment):
    reply = conversational_reply(question)

    assert reply is not None
    assert reply[0] == status
    assert fragment in reply[1]


@pytest.mark.parametrize(
    "question",
    [
        "Спасибо, а где библиотека НГУ?",
        "Как проверить оценку по физике в НГУ?",
        "Напиши код направления Прикладная математика в НГУ",
        "Сколько мест в общежитии НГУ?",
    ],
)
def test_subject_questions_are_not_swallowed(question):
    assert conversational_reply(question) is None


@pytest.mark.parametrize(
    ("prior", "question", "resolved"),
    [
        (
            "расскажи про нгту",
            "а мгу",
            "Расскажи кратко про университет МГУ по официальным сведениям.",
        ),
        (
            "Расскажи про НГУ",
            "а MIT?",
            "Расскажи кратко про университет Массачусетский технологический "
            "институт (MIT) по официальным сведениям.",
        ),
        (
            "Где находится главный корпус НГУ?",
            "а как называется вуз полностью?",
            "Каково полное официальное наименование вуза НГУ?",
        ),
    ],
)
def test_context_routes_institution_switch_without_inheriting_previous_subject(
    prior, question, resolved
):
    history: list[context.Turn] = [
        {"role": "user", "text": prior},
        {"role": "assistant", "text": "Ответ по предыдущему вузу."},
    ]

    assert context.resolve(question, history) == (resolved, "")


def test_model_can_reply_without_search_even_when_a_university_is_named(monkeypatch):
    def complete(_prompt, *, system_prompt, **_options):
        assert "помощник по вузам" in system_prompt

        return {"answer": "Здравствуйте! Прошу выдать справку."}

    def unexpected(*_args, **_kwargs):
        pytest.fail("Модель не запросила поиск.")

    monkeypatch.setattr(composition, "ask", complete)
    monkeypatch.setattr(composition, "_make_service", unexpected)

    result = composition.answer_question(
        "Составь короткое письмо в деканат НГУ для запроса справки"
    )

    assert result["status"] == "answered"
    assert "Прошу" in result["answer"]
    assert result["citations"] == []


def test_malformed_route_does_not_start_search(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Невалидное решение разрешило поиск.")

    raw = _web_call("Где находится НГУ?")
    raw["tool_calls"][0]["function"]["arguments"] = "не JSON"

    monkeypatch.setattr(composition, "ask", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(composition, "_make_service", unexpected)

    result = composition.answer_question("Сколько бюджетных мест в НГУ?")

    assert result["status"] == "clarification"


def test_unsupported_admission_campaign_cannot_pass_on_an_undated_quote():
    quote = "Приём документов завершается 25 июля."
    url = "https://example.org/admissions"
    result = {
        "status": "answered",
        "answer": "В 2027 году срок приёма документов — 25 июля.",
        "citations": [{"source": 1, "verified": True, "url": url, "quote": quote}],
        "malformed_citations": 0,
        "sources": [{"source": 1, "url": url, "title": "Кампания 2026", "text": quote}],
    }
    question = "До какой даты НГУ принимает документы на бакалавриат в 2027 году?"

    rejected = check_answer(question, result)
    result["sources"][0]["title"] = "Кампания 2027"

    assert rejected["status"] == "unverified"
    assert rejected["grounding_failure"] == "campaign"
    assert rejected["citations"] == []
    assert check_answer(question, result)["status"] == "answered"


def test_fact_claim_on_local_route_requires_explicit_search_decision(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Поиск запускается только через отдельный tool call.")

    answer = "Если тебе нужен адрес главного корпуса, скажи, о каком НГУ речь."

    monkeypatch.setattr(composition, "ask", lambda *_args, **_kwargs: {"answer": answer})
    monkeypatch.setattr(composition, "_make_service", unexpected)

    result = composition.answer_question("Привет, где находится главный корпус НГУ?")

    assert result["answer"] == answer


def test_search_discards_binary_preview_without_losing_the_document():
    corrupt: SearchResult = {
        "url": "https://example.org/page",
        "title": "Тест",
        "snippet": "húõ£" * 24,
    }
    normal: SearchResult = {
        "url": "https://example.org/cz",
        "title": "Text",
        "snippet": "Příliš žluťoučký kůň.",
    }

    assert evidence_sources([corrupt, normal]) == [{**corrupt, "snippet": ""}, normal]


@pytest.mark.parametrize(
    ("query", "snippet", "name"),
    [
        (
            "Расскажи про университет МГУ",
            "Московский государственный университет имени М.В.Ломоносова — вуз.",
            "Московский государственный университет имени М.В.Ломоносова",
        ),
        (
            "Как полностью называется MIT?",
            "The Massachusetts Institute of Technology (MIT) is an independent institution.",
            "Massachusetts Institute of Technology",
        ),
    ],
)
def test_overview_keeps_exact_institution_name_when_draft_has_bad_citations(query, snippet, name):
    sources: list[SearchResult] = [
        {"url": "https://university.edu/about", "title": "О вузе", "snippet": snippet},
    ]
    response = institution_fact(
        query, sources, {"status": "unverified", "active_skills": ["overview"]}
    )

    assert response is not None
    assert response["citations"][0]["quote"] == name
    assert response["citations"][0]["verified"]


def test_overview_abstains_when_abbreviation_has_two_different_expansions():
    sources: list[SearchResult] = [
        {
            "url": "https://example.edu/first",
            "title": "О вузе",
            "snippet": "Морской государственный университет.",
        },
        {
            "url": "https://example.edu/second",
            "title": "О вузе",
            "snippet": "Международный гуманитарный университет.",
        },
    ]

    assert institution_fact("Как полностью называется МГУ?", sources, {}) is None


def test_first_turn_overview_normalizes_lowercase_university():
    result = context.resolve("расскажи про нгту", [])

    assert result == ("Расскажи кратко про университет НГТУ по официальным сведениям.", "")


@pytest.mark.parametrize("alias", ["мит", "MIT", "а мит?"])
def test_mit_alias_after_unrelated_turn_keeps_the_right_university(alias):
    history: list[context.Turn] = [
        {"role": "user", "text": "чд"},
        {"role": "assistant", "text": "О каком вузе речь?"},
    ]

    result, clarification = context.resolve(alias, history)

    assert "Массачусетский технологический институт (MIT)" in result
    assert result.startswith("Расскажи кратко")
    assert not clarification


def test_mit_alias_can_complete_a_university_followup():
    history: list[context.Turn] = [
        {"role": "user", "text": "Где взять справку?"},
        {"role": "assistant", "text": "О каком вузе речь?"},
    ]

    result, clarification = context.resolve("мит", history)

    assert result.startswith("Где взять справку в вузе")
    assert "Массачусетский технологический институт (MIT)" in result
    assert not clarification


def test_mit_alias_is_expanded_inside_a_russian_question():
    result, clarification = context.resolve("Какие программы предлагает МИТ?", [])

    assert result == "Какие программы предлагает Массачусетский технологический институт (MIT)?"
    assert not clarification


def test_short_banter_keeps_original_text_for_conversation_routing():
    history: list[context.Turn] = [
        {"role": "user", "text": "лол ты грязнулька"},
        {"role": "assistant", "text": "Да уж, идеальным меня не назовёшь."},
    ]

    assert context.resolve("грязнульки мне нужны", history) == (
        "грязнульки мне нужны",
        "",
    )


def test_slang_about_europe_keeps_universities_in_the_search_query():
    question, clarification = context.resolve("братан топовые уникик европы какие", [])

    assert "университеты европы" in question
    assert not clarification


def test_audit_rejection_recovers_exact_institution_name():
    name = "Massachusetts Institute of Technology"
    source: SearchResult = {
        "url": "https://example.edu/about",
        "title": "MIT",
        "snippet": name,
    }

    def generate(*_args, **_options):
        return {
            "status": "answered",
            "answer": f"{name} заявляет, что он лучший университет мира.",
            "citations": [
                {"source": 1, "url": source["url"], "quote": name, "ref": 1, "verified": True}
            ],
            "malformed_citations": 0,
        }

    service = AnswerService(
        search=lambda _: [source],
        generate=generate,
        verify=lambda *_: (False, None),
        source_limit=1000,
        search_limit=5,
        recovery_limit=1,
    )
    result = service.answer("Как полностью называется MIT?")

    assert result["answer"] == f"Полное название — {name}〔1〕."
    assert result["citations"][0]["quote"] == name


@pytest.mark.parametrize("supported", [True, False])
def test_bad_citations_can_be_repaired_on_the_same_sources(supported):
    source: SearchResult = {
        "url": "https://example.edu/faculties",
        "title": "Факультеты",
        "snippet": "В состав университета входит исторический факультет.",
    }

    def draft(*_args, **_options):
        return {
            "status": "answered",
            "answer": "В университете 10 факультетов.",
            "citations": [],
            "malformed_citations": 1,
            "sources": [{"text": source["snippet"]}],
        }

    def repair(_question, results, **_options):
        assert results == [source]

        return {
            "status": "partial",
            "answer": "В университете есть исторический факультет〔1〕.",
            "citations": [
                {
                    "source": 1,
                    "ref": 1,
                    "url": source["url"],
                    "quote": source["snippet"],
                    "verified": True,
                }
            ],
            "malformed_citations": 0,
        }

    service = AnswerService(
        search=lambda _: [source],
        generate=draft,
        repair=repair,
        verify=lambda *_: (supported, None),
        source_limit=1000,
        search_limit=5,
        recovery_limit=1,
    )

    result = service.answer("Какие факультеты есть в университете?")

    if supported:
        assert result["status"] == "partial"
        assert result["citations"][0]["quote"] == source["snippet"]

    else:
        assert result["status"] == "unverified"
        assert result["citations"] == []


def test_conversation_sends_roles_and_current_turn():
    history: list[context.Turn] = [
        {"role": "user", "text": "Моя зачётка сбежала в отпуск."},
        {"role": "assistant", "text": "Заслуженный отпуск от оценок."},
    ]
    current = "а где находится главный корпус ТГУ в Томске?"

    def complete(question, *, history=None, tools=None, **_options):
        assert question == current
        assert history == [
            {"role": "user", "content": "Моя зачётка сбежала в отпуск."},
            {"role": "assistant", "content": "Заслуженный отпуск от оценок."},
        ]
        assert tools is not None
        assert [tool["function"]["name"] for tool in tools] == ["web_search"]

        return _web_call("Адрес главного корпуса ТГУ в Томске")

    reply = dialogue_reply(
        current, history, composition.load_settings(), attachment_text="", complete=complete
    )

    assert reply == {"search_question": "Адрес главного корпуса ТГУ в Томске"}


@pytest.mark.parametrize(("answer", "university"), [("мит", "MIT"), ("НГУ", "НГУ")])
def test_university_reply_leaves_unknown_term_to_clarify(monkeypatch, answer, university):
    history: list[context.Turn] = [
        {"role": "user", "text": "Что нужно, чтобы поступить на дс?"},
        {
            "role": "assistant",
            "text": "Уточни, какой вуз и что означает «дс» — дизайн или что-то ещё?",
        },
    ]

    def unexpected(*_args, **_kwargs):
        pytest.fail("До выяснения значения термина поиск и генерация не нужны.")

    monkeypatch.setattr(composition, "ask", unexpected)
    monkeypatch.setattr(composition, "_make_service", unexpected)

    result = composition.answer_question(answer, history=history)

    assert result["status"] == "clarification"
    assert university in result["answer"]
    assert "под «дс»" in result["answer"]
    assert result["citations"] == []


@pytest.mark.parametrize("repair", [False, True])
def test_supported_partial_admissions_answer_ends_with_question(repair):
    quote = "Graduate applicants must have a bachelor's degree or its equivalent."
    source: SearchResult = {
        "url": "https://example.edu/graduate",
        "title": "Graduate admissions",
        "snippet": quote,
    }

    def supported(_question, _results, **_options):
        return {
            "status": "partial",
            "answer": "Для поступления в магистратуру нужен диплом бакалавра или эквивалент〔1〕.",
            "follow_up": "Ты ищешь степень или онлайн-курсы?",
            "citations": [{"source": 1, "url": source["url"], "quote": quote, "verified": True}],
            "malformed_citations": 0,
        }

    def invalid(*_args, **_options):
        return {"status": "invalid", "answer": "", "citations": [], "malformed_citations": 0}

    def unexpected():
        pytest.fail("Уточнение пользователя нельзя заменить ещё одним веб-поиском.")

    service = AnswerService(
        search=lambda _: [source],
        generate=invalid if repair else supported,
        repair=supported,
        verify=lambda *_: (True, None),
        backup_search=unexpected,
        rewrite=lambda *_: ("новый запрос", None),
        source_limit=1000,
        search_limit=5,
        recovery_limit=1,
    )

    result = service.answer("Хочу туда после бакалавриата по информационной безопасности.")

    assert result["status"] == "partial"
    assert result["answer"].endswith("Ты ищешь степень или онлайн-курсы?")
    assert quote == result["citations"][0]["quote"]


@pytest.mark.parametrize(
    ("host", "status"),
    [("catalog.duke.edu", "partial"), ("catalog.other.edu", "unverified")],
)
def test_admissions_fallback_uses_the_destination_university(host, status):
    source: SearchResult = {
        "url": f"https://{host}/graduate-education/admissions",
        "title": "Graduate Admissions",
        "snippet": "An applicant must normally hold a bachelor's degree or its equivalent.",
    }

    def declined(*_args, **_options):
        return {
            "status": "unverified",
            "answer": "Не хватает информации о программе.",
            "citations": [],
            "malformed_citations": 0,
        }

    service = AnswerService(
        search=lambda _: [source],
        generate=declined,
        source_limit=1000,
        search_limit=5,
        recovery_limit=1,
    )

    result = service.answer("Как поступить в Duke после своего бакалавриата по ИБ?")

    assert result["status"] == status

    if status == "partial":
        assert "диплом бакалавра или его эквивалент" in result["answer"]
        assert result["answer"].endswith("?")
        assert result["citations"] and result["citations"][0]["url"] == source["url"]

    else:
        assert result["citations"] == []


def test_unverified_address_is_not_served_from_memory():
    search = _web_call("Где находится главный корпус НГУ?")

    search["tool_calls"].extend(_web_call("Какие есть факультеты НГУ?")["tool_calls"])

    results = iter(
        [
            {"answer": "Главный корпус НГУ находится на улице Пирогова, 1."},
            search,
        ]
    )

    def complete(*_args, **_options):
        return next(results)

    reply = dialogue_reply(
        "Где главный корпус НГУ?",
        [],
        composition.load_settings(),
        attachment_text="",
        complete=complete,
    )

    assert reply == {"search_question": "Где находится главный корпус НГУ?"}


def test_address_from_previous_turn_cannot_replace_current_name_lookup():
    response = _web_call("Адрес главного корпуса НГУ")
    current = "Полное официальное наименование НГУ"

    response["tool_calls"].extend(_web_call(current)["tool_calls"])

    def complete(*_args, **_options):
        return response

    result = dialogue_reply(
        "Как полностью называется НГУ?",
        [{"role": "user", "text": "Где находится главный корпус НГУ?"}],
        composition.load_settings(),
        attachment_text="",
        complete=complete,
    )

    assert result == {"search_question": current}


@pytest.mark.parametrize(
    "reaction", ["чел", "чеел", "БРО, да ладно", "ты серьёзно?", "грязнулька я", "😂"]
)
def test_short_reaction_responds_without_restarting_previous_search(monkeypatch, reaction):
    history: list[context.Turn] = [
        {"role": "user", "text": "Какие условия поступления в MIT?"},
        {"role": "assistant", "text": "Вот подробный список по поступлению."},
    ]
    answer = "Да, я тут переборщил с подробностями."

    def complete(question, *, tools, history, system_prompt, **_options):
        assert question == reaction
        assert tools is None
        assert history == []
        assert "В диалоге упоминался вуз MIT" in system_prompt
        assert "короткая разговорная реакция" in system_prompt

        return {"answer": answer}

    def unexpected(*_args, **_options):
        pytest.fail("Реакция запустила повторный поиск по предыдущей теме.")

    monkeypatch.setattr(composition, "ask", complete)
    monkeypatch.setattr(composition, "_make_service", unexpected)

    result = composition.answer_question(reaction, history=history)

    assert result == {"status": "answered", "answer": answer, "citations": []}


def test_conversational_address_does_not_hide_a_new_question():
    question = "Чел, а в МГУ какие сроки подачи документов в магистратуру?"

    def complete(*_args, tools=None, **_options):
        assert tools and tools[0]["function"]["name"] == "web_search"

        return _web_call("Какие сроки подачи документов в магистратуру МГУ?")

    result = dialogue_reply(
        question,
        [],
        composition.load_settings(),
        attachment_text="",
        complete=complete,
    )

    assert result == {"search_question": "Какие сроки подачи документов в магистратуру МГУ?"}


def test_casual_reply_preserves_a_short_previous_joke():
    history: list[context.Turn] = [
        {"role": "user", "text": "Пошути про студента на сессии."},
        {"role": "assistant", "text": "Кофе на сессии получает зачёт первым."},
    ]
    answer = "И на пересдачу будильник зовут."

    def complete(*_args, history=None, tools=None, **_options):
        assert history and history[-1]["content"] == "Кофе на сессии получает зачёт первым."
        assert tools is None

        return {"answer": answer}

    result = dialogue_reply(
        "да ладно", history, composition.load_settings(), attachment_text="", complete=complete
    )

    assert result["answer"] == answer


def test_discovery_retries_a_direct_claim_and_keeps_the_search_context():
    history: list[context.Turn] = [
        {"role": "user", "text": "Я из ВШЭ, смотрю программы на 2027"},
        {"role": "assistant", "text": "Понял."},
    ]
    outcomes = iter(
        [
            {"answer": "У ВШЭ есть акселератор, который сейчас принимает заявки."},
            _web_call("акселераторы для студентов ВШЭ 2025 2027"),
        ]
    )

    def complete(*_args, tools=None, system_prompt="", **_options):
        assert tools and tools[0]["function"]["name"] == "web_search"
        assert "просьба подобрать реальные возможности" in system_prompt

        return next(outcomes)

    result = dialogue_reply(
        "ищу акселераторы",
        history,
        composition.load_settings(),
        attachment_text="",
        complete=complete,
    )

    assert result == {"search_question": "акселераторы для студентов ВШЭ 2027", "discovery": True}


def test_discovery_accepts_a_clarification_without_starting_search():
    answer = "Ты про студенческие стипендии или гранты для проекта?"

    result = dialogue_reply(
        "подбираю стипендии",
        [],
        composition.load_settings(),
        attachment_text="",
        complete=lambda *_args, **_options: {"answer": answer},
    )

    assert result == {"status": "answered", "answer": answer, "citations": []}


def test_discovery_does_not_return_a_repeated_unverified_claim():
    result = dialogue_reply(
        "подбираю стипендии",
        [],
        composition.load_settings(),
        attachment_text="",
        complete=lambda *_args, **_options: {"answer": "Есть стипендия, заявки уже принимают."},
    )

    assert result["status"] == "clarification"


def test_decision_recognizes_an_implicit_request_without_running_search_itself():
    history: list[context.Turn] = [{"role": "user", "text": "Учусь в ИТМО, делаю стартап."}]

    def decide(question, recent):
        assert question == "хочу на что-нибудь подать проект"
        assert recent == [{"role": "user", "content": "Учусь в ИТМО, делаю стартап."}]

        return "search"

    def complete(*_args, tools=None, system_prompt="", **_options):
        assert tools and tools[0]["function"]["name"] == "web_search"
        assert "просьба подобрать реальные возможности" in system_prompt

        return _web_call("Куда подать студенческий стартап в ИТМО?")

    result = dialogue_reply(
        "хочу на что-нибудь подать проект",
        history,
        composition.load_settings(),
        attachment_text="",
        decide=decide,
        complete=complete,
    )

    assert result == {
        "search_question": "Куда подать студенческий стартап в ИТМО?",
        "discovery": True,
    }


@pytest.mark.parametrize("choice", ["reply", None])
def test_decision_reply_and_fallback_keep_the_conversation_usable(choice):
    def complete(*_args, tools=None, **_options):
        assert bool(tools) == (choice is None)

        return {"answer": "Могу помочь набросать письмо."}

    result = dialogue_reply(
        "хочу написать письмо",
        [],
        composition.load_settings(),
        attachment_text="",
        decide=lambda *_args: choice,
        complete=complete,
    )

    assert result["answer"] == "Могу помочь набросать письмо."


@pytest.mark.parametrize("approved", [True, False])
def test_independent_supported_option_survives_a_bad_citation_only_after_audit(approved):
    url = "https://example.edu/accelerator"
    quote = "В бизнес-акселераторе команды работают с наставниками."
    draft = {
        "status": "answered",
        "answer": "1. Бизнес-акселератор предлагает работу с наставниками〔1〕.\n"
        "2. Приём заявок уже открыт〔2〕.",
        "citations": [
            {"source": 1, "ref": 1, "url": url, "quote": quote, "verified": True},
            {"source": 2, "ref": 2, "url": url, "quote": "Регистрация открыта.", "verified": False},
        ],
        "malformed_citations": 0,
    }

    def verify(_, result):
        assert result["status"] == "partial"
        assert result["citations"] == draft["citations"][:1]
        assert "Приём заявок" not in result["answer"]

        return approved, None

    service = AnswerService(
        search=lambda _: [{"url": url, "title": "Бизнес-акселератор", "snippet": quote}],
        generate=lambda *_args, **_options: draft,
        verify=verify,
        source_limit=1000,
        search_limit=5,
        recovery_limit=1,
    )

    result = service.answer("Ищу акселераторы")

    if approved:
        assert result["status"] == "partial"
        assert result["citations"] == draft["citations"][:1]
        assert result["answer"].endswith("Какой вариант тебе интереснее уточнить?")
        assert "Приём заявок" not in result["answer"]
    else:
        assert result["status"] == "unverified"
        assert result["citations"] == []


def test_specialization_short_reply_keeps_program_discovery():
    history: list[context.Turn] = [
        {"role": "user", "text": "найди сам чето по направлению ит"},
        {"role": "assistant", "text": "Тебе ближе программирование, Data Science или игры?"},
    ]

    assert context.resolve("дс", history) == ("Подбери учебные программы по Data Science", "")
    assert context.resolve("дс", history[0:1]) == ("дс", "")
