"""Проверяет знакомство с Пигеоном и переход к фактическому вопросу."""

import json

import pytest
from fastapi.testclient import TestClient

from agent import composition
from agent.app import app
from agent.application import context
from agent.application.dialogue import dialogue_reply
from agent.application.generation import answer_with_source_ids
from agent.application.service import AnswerService
from agent.application.skills import ActiveSkills
from agent.domain.conversation import conversational_reply
from agent.domain.grounding import check_answer, keep_web_evidence
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
    assert "Новосибирска" in response.json()["answer"]
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
        lambda *_args, **_kwargs: AnswerService(
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
            "Расскажи кратко про университет MIT по официальным сведениям.",
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

        return {"answer": "Понял, ты учишься в НГУ."}

    def unexpected(*_args, **_kwargs):
        pytest.fail("Модель не запросила поиск.")

    monkeypatch.setattr(composition, "ask", complete)
    monkeypatch.setattr(composition, "_make_service", unexpected)

    result = composition.answer_question("Я учусь в НГУ.")

    assert result["status"] == "answered"
    assert result["answer"] == "Понял, ты учишься в НГУ."
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


@pytest.mark.parametrize(
    "question", ["расскажи про нгту", "Какие программы предлагает МИТ?", "братан уники европы"]
)
def test_user_question_keeps_original_names_for_domain_decision(question):
    assert context.resolve(question, []) == (question, "")


def test_short_banter_keeps_original_text_for_conversation_routing():
    history: list[context.Turn] = [
        {"role": "user", "text": "лол ты грязнулька"},
        {"role": "assistant", "text": "Да уж, идеальным меня не назовёшь."},
    ]

    assert context.resolve("грязнульки мне нужны", history) == (
        "грязнульки мне нужны",
        "",
    )


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


@pytest.mark.parametrize(("answer", "university"), [("мит", "мит"), ("НГУ", "НГУ")])
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
    "reaction",
    [
        "чел",
        "чеел",
        "БРО, да ладно",
        "ты серьёзно?",
        "грязнулька я",
        "лан, а если я грязнулька?",
        "😂",
    ],
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
        assert "Ответь одной фразой" in system_prompt

        return {"answer": answer}

    def unexpected(*_args, **_options):
        pytest.fail("Реакция запустила повторный поиск по предыдущей теме.")

    monkeypatch.setattr(composition, "decide", lambda *_args, **_kwargs: "banter")
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

    assert result == {
        "search_question": "Какие сроки подачи документов в магистратуру МГУ?",
        "web_policy": "admission",
    }


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
        assert "Подбери реальные варианты по открытым источникам" in system_prompt

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
        assert "Подбери реальные варианты по открытым источникам" in system_prompt

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


def test_research_continuation_retries_unsupported_facts_even_after_reply_decision():
    history: list[context.Turn] = [
        {"role": "user", "text": "хочу на магистратуру в Stanford по ИИ"},
        {"role": "assistant", "text": "Какая область тебя интересует?"},
    ]
    query = "Stanford master's student AI agents research labs"
    outcomes = iter([{"answer": "У них есть лаборатория и проект. Рассказать?"}, _web_call(query)])

    result = dialogue_reply(
        "ресерч в агентах",
        history,
        composition.load_settings(),
        attachment_text="",
        complete=lambda *_args, **_options: next(outcomes),
        decide=lambda *_args: "reply",
    )

    assert result == {"search_question": query, "discovery": True}


def test_submission_followup_asks_where_the_documents_go():
    history: list[context.Turn] = [
        {"role": "user", "text": "хочу на магистратуру"},
        {"role": "assistant", "text": "Документы: транскрипты и рекомендации〔1〕."},
    ]

    assert context.resolve("это куда", history) == (
        "Куда подавать документы для поступления на обсуждаемую программу?",
        "",
    )


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        ("Заявку подают через портал〔1〕.", "answered"),
        ("Заявку подают через портал〔5〕.", "unverified"),
        ("Заявку подают на https://apply.example.edu/wrong〔1〕.", "unverified"),
        ("Заявку подают на https://apply.example.edu/right〔1〕.", "answered"),
    ],
)
def test_inline_evidence_uses_citation_position_and_known_urls(answer, status):
    url = "https://apply.example.edu/right"
    draft = {
        "status": "answered",
        "answer": answer,
        "malformed_citations": 0,
        "citations": [
            {"source": 5, "ref": 1, "url": url, "quote": "Подайте заявку.", "verified": True}
        ],
    }

    assert check_answer("Куда подавать документы?", draft)["status"] == status


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ("Интересует магистратура или исследовательская группа?", "answered"),
        ("У них отбор 5%. Хочешь подробности?", "clarification"),
    ],
)
def test_brief_topic_cannot_publish_uncited_university_facts(reply, status):
    result = dialogue_reply(
        "стенфорд айи",
        [],
        composition.load_settings(),
        attachment_text="",
        complete=lambda *_args, **_options: {"answer": reply},
    )

    assert result["status"] == status
    assert "5%" not in result["answer"]


def test_unused_bad_citation_does_not_discard_a_cited_answer():
    url = "https://catalog.example.edu/program"
    good = {"source": 3, "ref": 1, "url": url, "quote": "MS in Computer Science", "verified": True}
    draft = {
        "status": "answered",
        "answer": "Программа MS in Computer Science〔1〕.",
        "malformed_citations": 0,
        "citations": [good, {**good, "ref": 2, "verified": False}],
    }

    checked = check_answer("Как называется программа?", draft)

    assert checked["status"] == "answered"
    assert checked["citations"] == [good]


@pytest.mark.parametrize("amount", ["90000 USD", "$90000", "90000 рублей"])
def test_web_dosearch_retains_cited_part_and_drops_unsupported_amount(amount):
    source: SearchResult = {
        "url": "https://example.edu/admissions",
        "title": "Admissions",
        "snippet": "Apply using the admissions portal.",
    }
    citation = {
        "source": 1,
        "ref": 1,
        "url": source["url"],
        "quote": source["snippet"],
        "verified": True,
    }

    def web_answer(*_args, **_options):
        return {
            "status": "partial",
            "answer": f"Заявку подают через портал〔1〕.\nОбучение стоит {amount}〔1〕.",
            "citations": [citation],
            "next_query": "стоимость программы",
            "gap": "Стоимость в доступных материалах не указана.",
            "next_step": "Подскажи год поступления, чтобы поискать тарифы.",
        }

    def backup_search():
        def search(query):
            assert query == "стоимость программы"

            return []

        return search

    service = AnswerService(
        search=lambda _: [source],
        generate=web_answer,
        web_answer=web_answer,
        verify=lambda *_: (False, None),
        backup_search=backup_search,
        source_limit=500,
        search_limit=5,
        recovery_limit=1,
    )

    result = service.answer("Куда подавать заявку и сколько стоит обучение?")

    assert result["status"] == "partial"
    assert result["answer"] == (
        "Заявку подают через портал〔1〕.\n\n"
        "Стоимость в доступных материалах не указана.\n\n"
        "Подскажи год поступления, чтобы поискать тарифы."
    )
    assert result["citations"] == [citation]


def test_campaign_keeps_the_link_and_removes_an_undated_deadline():
    first = {"source": 1, "ref": 1, "quote": "Документы подаются до 25 июля."}
    second = {"source": 2, "ref": 2, "quote": "Кампания 2027 года. Заявки принимаются онлайн."}
    checked = keep_web_evidence(
        "До какого числа подать документы в НГУ в 2027?",
        {
            "status": "answered",
            "answer": "Документы подаются до 25 июля〔1〕.\nПодать заявление можно онлайн〔2〕.",
            "citations": [first, second],
        },
    )

    assert checked["status"] == "partial"
    assert checked["answer"] == "Подать заявление можно онлайн〔2〕."
    assert checked["citations"] == [second]


@pytest.mark.parametrize(
    ("question", "query", "discovery"),
    [
        (
            "хочу на магу туда и ресерч по агентам. "
            "где подаваться и сколько стоит для набора 2032?",
            "Stanford graduate AI admissions application tuition 2032",
            True,
        ),
        ("расскажи чем MIT от МФТИ отличается", "сравнение вузов MIT и МФТИ", False),
        (
            "где в НГУ посмотреть список программ бакалавриата?",
            "НГУ официальный список программ бакалавриата",
            False,
        ),
        (
            "Где у НГУ правила подачи документов на бакалавриат?",
            "НГУ официальные правила подачи документов на бакалавриат",
            True,
        ),
    ],
)
def test_scoped_factual_followup_retries_when_model_answers_without_search(
    question, query, discovery
):
    history: list[context.Turn] = [
        {"role": "user", "text": "стенфорд айи"},
        {"role": "assistant", "text": "Можно посмотреть программы и лаборатории Стэнфорда."},
    ]
    outcomes = iter([{"answer": "Будет стоить 140000 долларов."}, _web_call(query)])

    result = dialogue_reply(
        question,
        history,
        composition.load_settings(),
        attachment_text="",
        complete=lambda *_args, **_options: next(outcomes),
    )

    assert result["search_question"] == query
    assert bool(result.get("discovery")) is discovery

    if "список программ" in question:
        assert result["web_policy"] == "official_page"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "перечисли всё, что ты умеешь",
            "Я Пигеон. Ищу информацию о вузах Новосибирска и других городов.",
        ),
        (
            "расскажи про листы в питоне",
            "Я по вузам. Могу помочь найти информацию о них.",
        ),
    ],
)
def test_profile_and_python_tutorial_stay_in_product_scope(monkeypatch, question, expected):
    def unexpected(*_args, **_options):
        pytest.fail("Эта реплика не должна уходить на генерацию или в поиск.")

    monkeypatch.setattr(composition, "decide", lambda *_args, **_kwargs: "off_topic")
    monkeypatch.setattr(composition, "ask", unexpected)
    monkeypatch.setattr(composition, "make_searcher", unexpected)

    result = composition.answer_question(
        question,
        history=[
            {"role": "assistant", "text": "Я умею объяснять Python и писать код."},
        ],
    )

    assert result["answer"] == expected
    assert result["citations"] == []


@pytest.mark.parametrize("choice", ["search", "reply", None])
def test_admission_after_service_falls_back_to_search_on_an_unverified_direct_answer(choice):
    question = (
        "я пришел с армии и хочу пойти в вуз на бакалавра как мне это сделать и есть ли поблажки"
    )

    def decide(current, _recent):
        assert current == question

        return choice

    result = dialogue_reply(
        question,
        [],
        composition.load_settings(),
        attachment_text="",
        decide=decide,
        complete=lambda *_args, **_options: {"answer": "Всем после службы положена квота 10%."},
    )

    assert result == {
        "search_question": question,
        "check_admission_rights": True,
        "web_policy": "admission",
    }


@pytest.mark.parametrize(
    ("question", "previous_question"),
    [
        ("как выиграть стипендию нети некст", ""),
        ("я про хакатон нгту", "как победить в neti next"),
    ],
)
def test_named_student_opportunity_falls_back_to_search(question, previous_question):
    history: list[context.Turn] = [
        {"role": "user", "text": previous_question},
        {"role": "assistant", "text": "Это стипендия от Positive Technologies."},
    ]

    def complete(*_args, **_options):
        return {"answer": "Хакатон идёт два дня, призы выплачивают каждый месяц."}

    result = dialogue_reply(
        question,
        history,
        composition.load_settings(),
        attachment_text="",
        complete=complete,
    )

    assert question in result["search_question"]
    assert "Positive Technologies" not in result["search_question"]

    if previous_question:
        assert "neti next" in result["search_question"].casefold()


@pytest.mark.parametrize("policy", ["official_page", "admission", "funding", "event", "search"])
def test_domain_decision_requires_search_even_if_answer_model_skips_tool(policy):
    question = "а у этого варианта как?"
    result = dialogue_reply(
        question,
        [],
        composition.load_settings(),
        attachment_text="",
        decide=lambda *_args: policy,
        complete=lambda *_args, **_options: {"answer": "Там всё стандартно."},
    )

    assert result["search_question"] == question

    if policy != "search":
        assert result["web_policy"] == policy


def test_reply_review_reroutes_unverified_claims_for_an_unseen_program():
    question = "и что дают победителям?"
    answer = "Победители получают ежегодную выплату и стажировку у организатора. " * 3

    def review(current, _recent, proposed):
        assert current == question
        assert proposed == answer.strip()

        return "funding"

    result = dialogue_reply(
        question,
        [],
        composition.load_settings(),
        attachment_text="",
        decide=lambda *_args: "reply",
        review_reply=review,
        complete=lambda *_args, **_options: {"answer": answer},
    )

    assert result == {"search_question": question, "web_policy": "funding"}


def test_wrong_subject_sources_do_not_become_the_answer():
    source: SearchResult = {
        "url": "https://example.edu/event",
        "title": "Конкурс другого вуза",
        "snippet": "Условия участия в другом конкурсе.",
    }

    def write(*_args, **_kwargs):
        return {
            "status": "answered",
            "answer": "Конкурс другого вуза проходит очно〔1〕.",
            "citations": [
                {"source": 1, "ref": 1, "url": source["url"], "quote": source["snippet"]}
            ],
        }

    service = AnswerService(
        search=lambda _: [source],
        generate=write,
        web_answer=write,
        decide_source_match=lambda *_args: "different",
        source_limit=500,
        search_limit=5,
        recovery_limit=1,
    )

    result = service.answer("Условия участия в моём конкурсе.")

    assert result["status"] == "clarification"
    assert result["citations"] == []
    assert "Конкурс другого вуза" not in result["answer"]


def test_web_event_keeps_location_when_team_size_is_not_supported():
    citation = {
        "source": 1,
        "ref": 1,
        "url": "https://example.edu/event",
        "quote": "Защита очная. Команды по 4 человека. На выступление даётся 5 минут.",
    }
    result = keep_web_evidence(
        "Как пройдёт защита?",
        {
            "status": "answered",
            "answer": "Защита очная〔1〕.\nКоманда от 2 до 4 человек〔1〕.",
            "citations": [citation],
        },
    )

    assert result["status"] == "partial"
    assert result["answer"] == "Защита очная〔1〕."
    assert result["citations"] == [citation]


@pytest.mark.parametrize("read_succeeds", [True, False])
def test_web_primary_page_preserves_search_evidence_and_adds_verified_passage(read_succeeds):
    url = "https://example.edu/fellowships"
    source: SearchResult = {
        "url": url,
        "title": "Fellowships",
        "snippet": "Applications open in January.",
    }
    passage = "There are five awards of €6,500 gross per academic year."
    calls = []

    def read(address):
        assert address == url

        calls.append("read")

        return "Fellowships\n" + passage if read_succeeds else ""

    def write(question, **_):
        fragment = json.loads(question)["search_fragments"][0]["text"]

        assert source["snippet"] in fragment
        assert (passage in fragment) == read_succeeds

        calls.append("write")

        return {"answer": '{"answer":"Заявки открываются в январе〔1〕.","status":"answered"}'}

    def review(question, **_):
        assert "draft" in json.loads(question)

        calls.append("review")

        answer = "Заявки открываются в январе〔1〕."

        if read_succeeds:
            answer += "\nВыплата — 6500 евро за учебный год〔1〕."

        return {
            "answer": json.dumps(
                {"answer": answer, "status": "partial", "source_quotes": {"1": passage}}
            )
        }

    question = "Как подать заявку на Fellowships и сколько платят?"
    result = answer_with_source_ids(
        question,
        [source],
        complete=write,
        system_prompt="",
        source_limit=500,
        select=lambda *_args, **_kwargs: ActiveSkills((), (), frozenset(), False),
        read=read,
        review_claims=review,
    )
    result = keep_web_evidence(question, result)

    assert calls == ["read", "write", "review"]
    assert "Заявки открываются в январе" in result["answer"]
    assert ("6500 евро" in result["answer"]) == read_succeeds
    assert result["citations"][0].get("display_quote") == (passage if read_succeeds else None)


def test_web_campaign_retains_explicitly_general_deadline_when_archive_is_missing():
    citation = {"source": 1, "ref": 1, "quote": "Applications typically close on March 31."}
    checked = keep_web_evidence(
        "Когда закончилась подача документов в 2026 году?",
        {
            "status": "partial",
            "answer": "Обычные сроки: подача до 31 марта〔1〕.\nВ 2026 подача до 31 марта〔1〕.",
            "gap": "Архив именно этой кампании не нашёл.",
            "citations": [citation],
        },
    )

    assert checked["answer"] == "Обычные сроки: подача до 31 марта〔1〕."
