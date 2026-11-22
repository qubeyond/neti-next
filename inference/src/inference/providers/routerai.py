"""Реализует операции моделей через HTTP API RouterAI."""

import base64
import json
import logging
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, closing
from dataclasses import asdict
from time import monotonic
from typing import cast

from httpx2 import HTTPError
from openai import (
    APIConnectionError,
    APIResponse,
    APIStatusError,
    APITimeoutError,
    DefaultHttpxClient,
    OpenAI,
)
from openai.types.chat import ChatCompletionMessageParam

from inference.config import RouterAIConfig
from inference.domain.entities import (
    Capabilities,
    ChoiceRequest,
    ChoiceResult,
    Completion,
    Generation,
    Operation,
    Recognition,
    Scan,
)
from inference.domain.errors import InferenceError
from inference.domain.ports import Provider
from inference.runtime.telemetry import DEADLINE, DISCONNECTED, event


class _DecisionResponse(APIResponse[dict]):
    pass


def _usage(result: dict) -> dict:
    value = result.get("usage")

    return value if isinstance(value, dict) else {}


class RouterAIProvider(Provider):
    def __init__(self, config: RouterAIConfig, client: OpenAI | None = None) -> None:
        self.config = config
        self.client = client
        key = config.api_key.get_secret_value()

        if self.client is None and key:
            self.client = OpenAI(
                api_key=key,
                base_url=str(config.base_url),
                timeout=config.timeout_seconds,
                max_retries=0,
                http_client=DefaultHttpxClient(follow_redirects=False),
            )

    def close(self) -> None:
        if self.client is not None:
            self.client.close()

    def _receive(
        self,
        endpoint: str,
        call: Callable[[OpenAI], AbstractContextManager[APIResponse]],
        *,
        timeout_seconds: int | None = None,
    ) -> dict:
        if self.client is None:
            raise InferenceError(503, "Провайдер не настроен.")

        end = DEADLINE.get()
        disconnected = DISCONNECTED.get()
        limit = timeout_seconds or self.config.timeout_seconds
        timeout = limit if end is None else min(limit, end - monotonic())

        if timeout <= 0 or (disconnected is not None and disconnected.is_set()):
            raise InferenceError(504, "Бюджет времени исчерпан.")

        try:
            with call(self.client.with_options(timeout=timeout)) as response:
                raw = bytearray()

                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if (end is not None and monotonic() >= end) or (
                        disconnected is not None and disconnected.is_set()
                    ):
                        raise InferenceError(504, "Бюджет времени исчерпан.")

                    if len(raw) + len(chunk) > self.config.max_response_bytes:
                        raise InferenceError(502, "Ответ провайдера превышает лимит размера.")

                    raw.extend(chunk)
        except APIStatusError as error:
            status = 429 if error.status_code == 429 else 502

            event(
                "provider.failed",
                logging.WARNING,
                provider="routerai",
                endpoint=endpoint,
                provider_status=error.status_code,
            )

            retry = error.response.headers.get("Retry-After", "")
            headers = (
                {"Retry-After": retry}
                if status == 429 and retry.isascii() and retry.isdecimal() and len(retry) <= 5
                else None
            )

            raise InferenceError(
                status, "Провайдер не смог обработать запрос.", headers=headers
            ) from None
        except APITimeoutError:
            raise InferenceError(504, "Время ожидания провайдера истекло.") from None
        except (APIConnectionError, HTTPError):
            raise InferenceError(502, "Провайдер недоступен.") from None

        try:
            result = json.loads(raw)
        except ValueError:
            raise InferenceError(502, "Провайдер вернул неверный ответ.") from None

        if not isinstance(result, dict):
            raise InferenceError(502, "Провайдер вернул неверный ответ.")

        return result

    def _chat(self, payload: dict) -> Completion:
        model = payload.get("model") or self.config.llm_model

        allowed = {
            value.strip()
            for value in self.config.inference_extra_models.split(",")
            if value.strip()
        }

        if model.lower().endswith(":online") or (
            model not in {self.config.llm_model, self.config.ocr_model} and model not in allowed
        ):
            raise InferenceError(422, "Модель недоступна для этой операции.")

        extra = {
            key: value
            for key, value in payload.items()
            if key not in {"model", "messages", "max_tokens"}
        }
        result = self._receive(
            "/chat/completions",
            lambda client: client.chat.completions.with_streaming_response.create(
                model=model,
                messages=cast(Iterable[ChatCompletionMessageParam], payload["messages"]),
                max_tokens=payload["max_tokens"],
                extra_body=extra,
            ),
        )

        try:
            choice = result["choices"][0]
            message = choice["message"]
            content = message.get("content")
            calls = message.get("tool_calls")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise InferenceError(502, "Провайдер не вернул ответ модели.") from None

        return Completion(
            answer=content if isinstance(content, str) else "",
            annotations=message.get("annotations") or [],
            model=result.get("model") or model,
            usage=_usage(result),
            finish_reason=choice.get("finish_reason"),
            tool_calls=[call for call in calls if isinstance(call, dict)]
            if isinstance(calls, list)
            else [],
        )

    def generate(self, request: Generation) -> Completion:
        payload = {key: value for key, value in asdict(request).items() if value is not None}

        return self._chat(payload)

    def transcribe(self, data: bytes, audio_format: str) -> Recognition:
        result = self._receive(
            "/audio/transcriptions",
            lambda client: client.audio.transcriptions.with_streaming_response.create(
                model=self.config.asr_model,
                language=self.config.asr_language,
                file=(f"audio.{audio_format}", data),
            ),
        )

        if not isinstance(result.get("text"), str):
            raise InferenceError(502, "Провайдер не вернул расшифровку.")

        return Recognition(
            result["text"], result.get("model") or self.config.asr_model, _usage(result)
        )

    def recognize_image(self, data: bytes, mime: str) -> Recognition:
        encoded = base64.b64encode(data).decode()
        result = self._chat(
            {
                "model": self.config.ocr_model,
                "max_tokens": self.config.ocr_max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.config.ocr_prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{encoded}"},
                            },
                        ],
                    }
                ],
            }
        )

        return Recognition(result.answer, result.model, result.usage)

    def recognize_scan(self, data: bytes) -> Scan:
        encoded = base64.b64encode(data).decode()
        result = self._chat(
            {
                "model": self.config.llm_model,
                "max_tokens": self.config.pdf_ocr_max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.config.pdf_ocr_prompt},
                            {
                                "type": "file",
                                "file": {
                                    "filename": "scan.pdf",
                                    "file_data": f"data:application/pdf;base64,{encoded}",
                                },
                            },
                        ],
                    }
                ],
                "plugins": [{"id": "file-parser", "pdf": {"engine": self.config.pdf_ocr_engine}}],
            }
        )
        texts = [
            item["text"]
            for annotation in result.annotations
            for item in annotation.get("file", {}).get("content", [])
            if item.get("type") == "text" and item.get("text")
        ]

        if not texts:
            raise InferenceError(502, "Провайдер не вернул извлечённый текст PDF.")

        return Scan(texts, self.config.pdf_ocr_engine, result.usage)

    def decide(self, request: ChoiceRequest) -> ChoiceResult:
        endpoint = "/decisions"
        model = self.config.decision_model
        payload = {
            "model": model,
            "state": request.state,
            "questions": {
                "choice": {
                    "type": "choice",
                    "instructions": request.instructions,
                    "criteria": request.choices,
                }
            },
        }
        result = self._receive(
            endpoint,
            lambda client: closing(
                client.post(
                    endpoint,
                    body=payload,
                    cast_to=_DecisionResponse,
                    options={"headers": {"X-Stainless-Raw-Response": "stream"}},
                )
            ),
            timeout_seconds=self.config.decision_timeout_seconds,
        )
        answers = result.get("answers")
        answer = answers.get("choice") if isinstance(answers, dict) else None
        choice = answer.get("choice") if isinstance(answer, dict) else None

        if not isinstance(choice, str) or choice not in request.choices:
            raise InferenceError(502, "Провайдер вернул неизвестный вариант.")

        raw_usage = _usage(result)
        usage = {
            metric: raw_usage[key]
            for key, metric in (
                ("input_tokens", "prompt_tokens"),
                ("output_tokens", "completion_tokens"),
                ("cost", "cost"),
            )
            if key in raw_usage
        }

        return ChoiceResult(choice, model, usage)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider="routerai",
            configured=self.client is not None,
            models={
                Operation.LLM: self.config.llm_model,
                Operation.ASR: self.config.asr_model,
                Operation.OCR: self.config.ocr_model,
                Operation.DECISION: self.config.decision_model,
            },
        )
