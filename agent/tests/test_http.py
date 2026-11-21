"""Проверяет HTTP-контракт и сборку сценария ответов."""

import json

from fastapi.testclient import TestClient

from agent import composition
from agent.app import app, create_app
from agent.shared.errors import AgentError

client = TestClient(app)


def test_health_and_disabled_answers(monkeypatch):
    monkeypatch.delenv("WEB_ANSWERS_ENABLED", raising=False)

    assert client.get("/agent/health").json() == {"status": "ok"}
    assert client.post("/agent/v1/answers", json={"question": "Вопрос"}).status_code == 503


def test_answer_returns_only_verified_citations(monkeypatch):
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")

    def answer(question):
        assert question == "Вопрос"

        return {
            "status": "answered",
            "answer": "Ответ",
            "citations": [
                {"url": "https://example.org", "quote": "Цитата", "verified": True},
                {"url": "https://example.org", "quote": "Ошибка", "verified": False},
            ],
            "generation": {"usage": {"cost": 1}},
        }

    response = TestClient(create_app(answer)).post(
        "/agent/v1/answers", json={"question": " Вопрос "}
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "answer": "Ответ",
        "citations": [{"url": "https://example.org", "quote": "Цитата"}],
    }


def test_validation_and_provider_error(monkeypatch):
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")

    def fail(_question):
        raise AgentError("Внутреннее сообщение")

    client = TestClient(create_app(fail))

    assert client.post("/agent/v1/answers", json={"question": "  "}).status_code == 422

    response = client.post("/agent/v1/answers", json={"question": "Вопрос"})

    assert response.status_code == 502
    assert "Внутреннее сообщение" not in response.text


def test_http_wires_search_and_generation_through_application(monkeypatch):
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")
    monkeypatch.setenv("WEB_RECOVERY_READ", "false")
    monkeypatch.setenv("WEB_RECOVERY_SEARCH", "false")
    monkeypatch.setenv("WEB_SOURCE_MAX_CHARS", "1500")
    monkeypatch.setenv("WEB_SYSTEM_PROMPT", "Отвечай по источникам.")
    monkeypatch.setattr(composition, "_local_settings", dict)

    def search(question):
        assert question == "Где библиотека?"

        return [
            {
                "url": "https://example.org/library",
                "title": "Библиотека",
                "snippet": "<p>Библиотека в корпусе 2.</p>",
            }
        ]

    def complete(prompt, **options):
        assert options["system_prompt"] == "Отвечай по источникам."
        assert json.loads(prompt)["search_fragments"][0]["text"] == ("Библиотека в корпусе 2.")

        return {
            "answer": json.dumps(
                {
                    "status": "answered",
                    "answer": "В корпусе 2.",
                    "citations": [{"source": 1, "quote": "Библиотека в корпусе 2."}],
                }
            )
        }

    monkeypatch.setattr(composition, "make_searcher", lambda: search)
    monkeypatch.setattr(composition, "ask", complete)

    response = client.post("/agent/v1/answers", json={"question": "Где библиотека?"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "answered",
        "answer": "В корпусе 2.",
        "citations": [{"url": "https://example.org/library", "quote": "Библиотека в корпусе 2."}],
    }
