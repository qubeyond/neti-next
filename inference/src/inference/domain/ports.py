"""Определяет контракт адаптера моделей."""

from typing import Protocol

from inference.domain.entities import (
    Capabilities,
    ChoiceRequest,
    ChoiceResult,
    Completion,
    Generation,
    Recognition,
    Scan,
)


class Provider(Protocol):
    def generate(self, request: Generation) -> Completion: ...

    def decide(self, request: ChoiceRequest) -> ChoiceResult: ...

    def transcribe(self, data: bytes, audio_format: str) -> Recognition: ...

    def recognize_image(self, data: bytes, mime: str) -> Recognition: ...

    def recognize_scan(self, data: bytes) -> Scan: ...

    def capabilities(self) -> Capabilities: ...
