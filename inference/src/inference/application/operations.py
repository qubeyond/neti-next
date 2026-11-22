"""Валидирует параметры операций и связывает выполнение с адаптером."""

import base64
import binascii

from inference.config import Limits
from inference.domain.entities import (
    Capabilities,
    ChoiceRequest,
    ChoiceResult,
    Completion,
    Generation,
    Operation,
    Recognition,
)
from inference.domain.errors import InferenceError
from inference.domain.ports import Provider
from inference.runtime.execution import Execution, Timed


class Operations:
    def __init__(self, provider: Provider, execution: Execution, limits: Limits) -> None:
        self.provider = provider
        self.execution = execution
        self.limits = limits

    def capabilities(self) -> Capabilities:
        return self.provider.capabilities()

    def generate(self, request: Generation) -> Timed[Completion]:
        if (
            len(request.messages) > self.limits.max_messages
            or any(
                len(message.content) > self.limits.max_message_chars for message in request.messages
            )
            or sum(len(message.content) for message in request.messages)
            > self.limits.max_total_input_chars
            or len(request.model or "") > self.limits.max_model_chars
            or request.max_tokens > self.limits.max_output_tokens
        ):
            raise InferenceError(422, "Превышен лимит запроса генерации.")

        return self.execution.perform(Operation.LLM, lambda: self.provider.generate(request))

    def decide(self, request: ChoiceRequest) -> Timed[ChoiceResult]:
        return self.execution.perform(Operation.DECISION, lambda: self.provider.decide(request))

    def transcribe(self, encoded: str, mime: str) -> Timed[Recognition]:
        if not mime.startswith("audio/"):
            raise InferenceError(415, "Нужен аудиофайл.")

        data = self._decode_media(encoded)
        subtype = mime.split("/", 1)[1]
        audio_format = {"mpeg": "mp3", "mp4": "m4a"}.get(subtype, subtype)

        return self.execution.perform(
            Operation.ASR, lambda: self.provider.transcribe(data, audio_format)
        )

    def recognize(self, encoded: str, mime: str) -> Timed[Recognition]:
        if not mime.startswith("image/") and mime != "application/pdf":
            raise InferenceError(415, "Нужно изображение или подготовленная страница скана.")

        data = self._decode_media(encoded)

        if mime == "application/pdf":
            return self.execution.perform(Operation.OCR, lambda: self._recognize_scan(data))

        return self.execution.perform(
            Operation.OCR, lambda: self.provider.recognize_image(data, mime)
        )

    def metrics(self) -> bytes:
        return self.execution.metrics()

    def _recognize_scan(self, data: bytes) -> Recognition:
        result = self.provider.recognize_scan(data)

        return Recognition("\n\n".join(result.fragments), result.model, result.usage)

    def _decode_media(self, encoded: str) -> bytes:
        if len(encoded) > self.limits.max_encoded_bytes:
            raise InferenceError(413, "Файл превышает лимит.")

        try:
            data = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            raise InferenceError(422, "Неверная кодировка файла.") from None

        if not data or len(data) > self.limits.max_file_bytes:
            raise InferenceError(413, "Файл превышает лимит.")

        return data
