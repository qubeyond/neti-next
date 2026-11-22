"""Проверяет передачу предпочтений в модель и изоляцию запросов."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from agent import composition
from agent.app import create_app
from agent.config import USER_INSTRUCTIONS
from agent.providers import inference


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    settings = {
        "INFERENCE_URL": "http://inference.invalid",
        "INFERENCE_TIMEOUT_SECONDS": "10",
        "AGENT_MAX_OUTPUT_TOKENS": "128",
        "AGENT_TURN_TIMEOUT_SECONDS": "30",
    }

    monkeypatch.setattr(composition, "load_settings", lambda: settings)
    monkeypatch.setattr(inference, "load_settings", lambda: settings)

    return settings


def test_profile_preferences_reach_model_and_reset(monkeypatch):
    payloads = []

    def request(_endpoint, _headers, payload, _timeout):
        payloads.append(payload)
        return {"answer": "Ответ"}

    def answer(*_args, **_kwargs):
        return inference.ask("Вопрос", system_prompt="Системные правила")

    monkeypatch.setattr(inference, "_request", request)
    monkeypatch.setattr(composition, "_answer_turn", answer)

    composition.answer_question("Вопрос", user_instructions="Отвечай кратко")
    composition.answer_question("Вопрос")

    assert payloads[0]["messages"][0]["content"] == "Системные правила"
    assert payloads[0]["messages"][1]["role"] == "user"
    assert "Отвечай кратко" in payloads[0]["messages"][1]["content"]
    assert len(payloads[1]["messages"]) == 2
    assert USER_INSTRUCTIONS.get() == ""


def test_profile_preferences_reset_after_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        assert USER_INSTRUCTIONS.get() == "Отвечай кратко"
        raise RuntimeError("Сбой модели")

    monkeypatch.setattr(composition, "_answer_turn", fail)

    with pytest.raises(RuntimeError):
        composition.answer_question("Вопрос", user_instructions="Отвечай кратко")

    assert USER_INSTRUCTIONS.get() == ""


def test_profile_preferences_apply_to_greeting(monkeypatch, isolated_settings):
    settings = {
        **isolated_settings,
        "DIALOGUE_ROUTING_PROMPT": "Отвечай на реплику пользователя.",
    }
    payloads = []

    def request(_endpoint, _headers, payload, _timeout):
        payloads.append(payload)
        return {"answer": "Здравствуйте! Чем могу помочь?"}

    monkeypatch.setattr(composition, "load_settings", lambda: settings)
    monkeypatch.setattr(composition, "decide", lambda *_args, **_kwargs: "reply")
    monkeypatch.setattr(inference, "_request", request)

    result = composition.answer_question("Привет", user_instructions="Обращайся на вы")

    assert result["answer"] == "Здравствуйте! Чем могу помочь?"
    assert "Обращайся на вы" in payloads[0]["messages"][1]["content"]


def test_concurrent_chats_keep_separate_preferences_and_history(monkeypatch):
    ready = Barrier(2)

    def request(_endpoint, _headers, payload, _timeout):
        return {**payload, "answer": "Ответ"}

    def answer(question, _model, _attachment, _quote, history, _settings, _image):
        ready.wait(timeout=5)

        return inference.ask(
            question,
            system_prompt="Системные правила",
            history=[{"role": turn["role"], "content": turn["text"]} for turn in history],
        )

    def run(marker):
        result = composition.answer_question(
            f"Вопрос {marker}",
            user_instructions=f"Инструкция {marker}",
            history=[{"role": "user", "text": f"История {marker}"}],
        )

        assert USER_INSTRUCTIONS.get() == ""

        return result["messages"]

    monkeypatch.setattr(inference, "_request", request)
    monkeypatch.setattr(composition, "_answer_turn", answer)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["чат-А", "чат-Б"]))

    for marker, messages in zip(["чат-А", "чат-Б"], results, strict=True):
        assert len(messages) == 4
        assert messages[0]["content"] == "Системные правила"
        assert messages[1]["content"].endswith(f"Инструкция {marker}")
        assert messages[2] == {"role": "user", "content": f"История {marker}"}
        assert messages[3] == {"role": "user", "content": f"Вопрос {marker}"}


def test_http_accepts_profile_preferences(monkeypatch):
    monkeypatch.setenv("WEB_ANSWERS_ENABLED", "true")
    monkeypatch.setenv("AGENT_TOKEN", "")

    def answer(question, *, user_instructions):
        assert question == "Вопрос"
        assert user_instructions == "Пиши списком"

        return {"status": "answered", "answer": "Ответ", "citations": []}

    client = TestClient(create_app(answer))

    assert (
        client.post(
            "/agent/v1/answers",
            json={"question": "Вопрос", "user_instructions": "Пиши списком"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/agent/v1/answers",
            json={"question": "Вопрос", "user_instructions": "я" * 2001},
        ).status_code
        == 422
    )
