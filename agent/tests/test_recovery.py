"""Проверяет бюджет восстановления и сохранение рабочего ответа."""

from agent.modules.answers.application.service import AnswerService
from agent.modules.answers.domain.recovery import document_excerpt, read_candidate
from agent.modules.answers.domain.sources import SearchResult
from agent.modules.answers.infrastructure import query_rewrite as recovery
from agent.shared.errors import AgentError


def source(url: str, snippet: str, title: str = "Документ") -> SearchResult:
    return {"url": url, "title": title, "snippet": snippet}


def response(status, verified=False):
    return {
        "status": status,
        "answer": "Ответ",
        "citations": [{"verified": True}] if verified else [],
        "malformed_citations": 0,
        "generation": {"usage": {"total_tokens": 100}},
    }


def test_read_candidate_prefers_dated_document_and_keeps_one_program():
    question = "Какой профиль у направления 09.03.03 в заявлении 2024 года?"
    candidates = [
        source("https://example.org/profiles", "09.03.03 — новый профиль"),
        source("https://example.org/form-2024.pdf", "09.03.03 — архивная запись"),
    ]
    document = (
        "09.03.02 Другая программа\n"
        "Не тот профиль\n"
        "09.03.03 Прикладная информатика\n"
        "Профиль из заявления\n"
        "11.03.02 Другая программа"
    )

    assert read_candidate(question, candidates) == 1
    assert document_excerpt(document, question, 1500) == (
        "09.03.03 Прикладная информатика\nПрофиль из заявления"
    )


def test_successful_answer_does_not_run_recovery():
    def unexpected(*args, **kwargs):
        raise AssertionError("Лишний вызов")

    service = AnswerService(
        search=unexpected,
        generate=unexpected,
        read=unexpected,
        backup_search=unexpected,
        rewrite=unexpected,
    )
    initial = response("answered", verified=True)

    assert service.recover("Вопрос", [], initial) is initial

    uncited = response("answered")

    assert service.recover("Вопрос", [], uncited) is uncited


def test_search_recovery_keeps_primary_and_limits_new_sources():
    results = [source(f"https://example.org/{i}", "Исходный") for i in range(5)]
    calls = []

    def search(query):
        calls.append(query)

        return [
            source("https://www.example.org/0/?utm_source=new", "Дубликат"),
            source("https://example.org/new1", "Новый"),
            source("https://example.org/new2", "Новый"),
            source("https://example.org/new3", "Лишний"),
        ]

    def generate(question, updated, /, *, model=None):
        assert question == "Где взять справку?"
        assert updated[:3] == results[:3]
        assert [row["url"] for row in updated[3:]] == [
            "https://example.org/new1",
            "https://example.org/new2",
        ]

        return response("answered", verified=True)

    service = AnswerService(
        search=search,
        generate=generate,
        backup_search=lambda: search,
        rewrite=lambda question, sources: ("взять справку?", None),
    )
    result = service.recover("Где взять справку?", results, response("partial"))

    assert calls == ["взять справку?"]
    assert result["recovery"]["outcome"] == "accepted_retry"


def test_rewrite_retains_year_and_program_code(monkeypatch):
    monkeypatch.setattr(
        recovery, "ask", lambda *args, **kwargs: {"answer": '{"query":"Профиль 2025"}'}
    )

    query, _ = recovery.rewrite_query("Какой профиль 09.03.03 в 2024 году?", [])

    assert query == "профиль 09.03.03 в 2024 году?"


def test_read_failure_preserves_initial_answer_without_second_action():
    def fail(url):
        raise AgentError("Ошибка чтения")

    def unexpected(*args, **kwargs):
        raise AssertionError("Дополнительное действие уже было")

    service = AnswerService(
        search=unexpected,
        generate=unexpected,
        read=fail,
        backup_search=unexpected,
        rewrite=unexpected,
    )
    initial = response("partial", verified=True)
    result = service.recover(
        "Какой профиль у направления 09.03.03?",
        [source("https://example.org/form.pdf", "09.03.03 — архивная запись")],
        initial,
    )

    assert result["status"] == initial["status"]
    assert result["citations"] == initial["citations"]
    assert result["recovery"]["outcome"] == "provider_error"
