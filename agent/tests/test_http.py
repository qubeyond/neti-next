from fastapi.testclient import TestClient

from agent import http
from agent.config import AgentError

client = TestClient(http.app)


def test_health_and_disabled_answers(monkeypatch):
    monkeypatch.delenv("WEB_ANSWERS_ENABLED", raising=False)
    assert client.get("/agent/health").json() == {"status": "ok"}
    assert (
        client.post("/agent/v1/answers", json={"question": "Вопрос"}).status_code == 503
    )


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

    monkeypatch.setattr(http, "answer_question", answer)
    response = client.post("/agent/v1/answers", json={"question": " Вопрос "})
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

    monkeypatch.setattr(http, "answer_question", fail)
    assert client.post("/agent/v1/answers", json={"question": "  "}).status_code == 422
    response = client.post("/agent/v1/answers", json={"question": "Вопрос"})
    assert response.status_code == 502
    assert "Внутреннее сообщение" not in response.text
