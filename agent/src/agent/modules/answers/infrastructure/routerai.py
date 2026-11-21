"""Выполняет запросы к текстовой модели RouterAI."""

import json
import os
from ssl import SSLError
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agent.shared.config import _local_settings, _positive_int, _setting
from agent.shared.errors import AgentError

from .network import open_on_interface


def ask(
    question: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
    allow_empty: bool = False,
    max_tokens: int | None = None,
    response_format: dict | None = None,
    reasoning_enabled: bool | None = None,
) -> dict:
    question = question.strip()

    if not question:
        raise AgentError("Вопрос не должен быть пустым.")

    settings = _local_settings()
    api_key = _setting(settings, "ROUTERAI_API_KEY")
    selected_model = (model or "").strip() or _setting(settings, "ROUTERAI_MODEL")

    if selected_model.lower().endswith(":online"):
        raise AgentError(
            "Выберите модель без суффикса :online: встроенный поиск RouterAI не используется."
        )

    endpoint = _setting(settings, "ROUTERAI_ENDPOINT")

    try:
        parsed_endpoint = urlsplit(endpoint)
        valid_endpoint = (
            parsed_endpoint.scheme == "https"
            and parsed_endpoint.hostname
            and parsed_endpoint.username is None
            and parsed_endpoint.password is None
        )
    except ValueError:
        valid_endpoint = False

    if not valid_endpoint:
        raise AgentError("ROUTERAI_ENDPOINT должен быть HTTPS-адресом без учётных данных.")

    timeout = _positive_int(settings, "ROUTERAI_TIMEOUT_SECONDS")
    interface = (
        os.getenv("ROUTERAI_NETWORK_INTERFACE", "").strip()
        or settings.get("ROUTERAI_NETWORK_INTERFACE", "").strip()
    )
    payload = {
        "model": selected_model,
        "max_tokens": max_tokens
        if max_tokens is not None
        else _positive_int(settings, "ROUTERAI_MAX_TOKENS"),
        "messages": [
            {
                "role": "system",
                "content": system_prompt or _setting(settings, "ROUTERAI_SYSTEM_PROMPT"),
            },
            {"role": "user", "content": question},
        ],
    }

    if response_format is not None:
        payload["response_format"] = response_format

    if reasoning_enabled is not None:
        payload["reasoning"] = {"enabled": reasoning_enabled}

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    started_at = perf_counter()

    try:
        transport = (
            open_on_interface(request, timeout, interface)
            if interface
            else urlopen(request, timeout=timeout)
        )

        with transport as response:
            result = json.load(response)
    except HTTPError as error:
        raise AgentError(f"RouterAI отклонил запрос: HTTP {error.code}.") from None
    except (URLError, SSLError) as error:
        if isinstance(getattr(error, "reason", error), SSLError):
            raise AgentError(
                "TLS-соединение с RouterAI не установилось (HTTP-ответа нет)."
            ) from None

        raise AgentError("Не удалось получить ответ от RouterAI.") from None
    except TimeoutError:
        raise AgentError("Не удалось получить ответ от RouterAI.") from None
    except ValueError:
        raise AgentError("RouterAI вернул некорректный JSON.") from None

    try:
        message = result["choices"][0]["message"]
        answer = message["content"]
    except (KeyError, IndexError, TypeError):
        raise AgentError("RouterAI не вернул ответ модели.") from None

    if not isinstance(answer, str):
        answer = ""

    if not answer.strip() and not allow_empty:
        raise AgentError("RouterAI вернул пустой ответ модели.")

    return {
        "answer": answer,
        "annotations": message.get("annotations") or [],
        "model": result.get("model") or selected_model,
        "usage": result.get("usage") or {},
        "finish_reason": result["choices"][0].get("finish_reason"),
        "latency_ms": round((perf_counter() - started_at) * 1000),
    }
