"""Вызывает текстовую модель через внутренний сервис инференса."""

import json
from math import isfinite
from time import perf_counter

from httpx2 import HTTPError, HTTPStatusError, TimeoutException

from agent.config import load_settings, optional, positive_int, product_prompt, required
from agent.domain.errors import AgentError
from agent.domain.ports import ChatMessage
from agent.providers.http import client
from agent.runtime import phase, remaining_time, upstream_headers


def ask(
    question: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    allow_empty: bool = False,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    reasoning_enabled: bool | None = None,
    history: list[ChatMessage] | None = None,
    tools: list[dict] | None = None,
    settings: dict[str, str] | None = None,
) -> dict:
    question = question.strip()

    if not question:
        raise AgentError("Вопрос не должен быть пустым.")

    settings = load_settings() if settings is None else settings
    endpoint = required(settings, "INFERENCE_URL").rstrip("/") + "/inference/v1/generate"
    timeout = positive_int(settings, "INFERENCE_TIMEOUT_SECONDS")
    token = optional(settings, "INFERENCE_TOKEN")
    headers = upstream_headers()

    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": model,
        "max_tokens": max_tokens
        if max_tokens is not None
        else positive_int(settings, "AGENT_MAX_OUTPUT_TOKENS"),
        "messages": [
            {
                "role": "system",
                "content": system_prompt
                or product_prompt(required(settings, "TEXT_SYSTEM_PROMPT")),
            },
            *(history or []),
            {"role": "user", "content": question},
        ],
    }

    if response_format is not None:
        payload["response_format"] = response_format

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    if reasoning_enabled is not None:
        payload["reasoning"] = {"enabled": reasoning_enabled}

    started_at = perf_counter()

    with phase("generate", service="inference") as trace:
        result = _request(endpoint, headers, payload, timeout)
        usage = result.get("usage")

        if isinstance(usage, dict):
            trace["usage"] = {
                name: value
                for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
                if isinstance(value := usage.get(name), (int, float))
                and not isinstance(value, bool)
                and 0 <= value
                and isfinite(value)
            }

        if isinstance(name := result.get("model"), str):
            trace["model"] = name[:100]

        if type(inference_ms := result.get("latency_ms")) is int and inference_ms >= 0:
            trace["upstream_ms"] = inference_ms

    answer = result.get("answer")

    if not isinstance(answer, str):
        answer = ""

    if not answer.strip() and not allow_empty:
        raise AgentError("Сервис инференса вернул пустой ответ модели.")

    return {
        **result,
        "answer": answer,
        "latency_ms": round((perf_counter() - started_at) * 1000),
    }


def decide(question: str, history: list[ChatMessage], *, settings: dict[str, str]) -> str | None:
    endpoint = required(settings, "INFERENCE_URL").rstrip("/") + "/inference/v1/decide"
    timeout = positive_int(settings, "AGENT_DECISION_TIMEOUT_SECONDS")
    headers = upstream_headers()

    if token := optional(settings, "INFERENCE_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "state": json.dumps({"current": question, "history": history}, ensure_ascii=False),
        "instructions": required(settings, "DECISION_ROUTING_PROMPT"),
        "choices": {
            "search": "Нужны новые сведения по вузам, обучению или возможностям для студентов.",
            "reply": "Можно ответить по контексту или продолжить разговор без новых сведений.",
        },
    }

    with phase("decision", service="inference") as trace:
        try:
            result = _request(endpoint, headers, payload, timeout)
            choice = result.get("choice")
        except AgentError:
            choice = None

        trace["decision"] = choice if choice in ("search", "reply") else "fallback"

    return choice if choice in ("search", "reply") else None


def _request(endpoint: str, headers: dict, payload: dict, timeout: int) -> dict:
    try:
        response = client.post(
            endpoint, json=payload, headers=headers, timeout=remaining_time(timeout)
        )

        response.raise_for_status()

        result = response.json()
    except HTTPStatusError as error:
        status = error.response.status_code

        raise AgentError(
            "Инференс отклонил запрос.", status=status if status in {429, 503, 504} else 502
        ) from None
    except TimeoutException:
        raise AgentError("Время ожидания инференса истекло.", status=504) from None
    except (HTTPError, ValueError):
        raise AgentError("Не удалось получить ответ от сервиса инференса.") from None

    if not isinstance(result, dict):
        raise AgentError("Сервис инференса вернул неверный ответ.")

    return result
