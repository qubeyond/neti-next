"""Хранит независимые от HTTP и провайдеров сущности инференса."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class Operation(StrEnum):
    LLM = "llm"
    ASR = "asr"
    OCR = "ocr"
    DECISION = "decision"


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Generation:
    messages: list[Message]
    max_tokens: int
    model: str | None = None
    response_format: dict | None = None
    reasoning: dict | None = None
    tools: list[dict] | None = None
    tool_choice: Literal["auto", "none"] | None = None


@dataclass(frozen=True)
class Completion:
    answer: str
    model: str
    usage: dict = field(default_factory=dict)
    annotations: list = field(default_factory=list)
    finish_reason: str | None = None
    tool_calls: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ChoiceRequest:
    state: str
    instructions: str
    choices: dict[str, str]


@dataclass(frozen=True)
class ChoiceResult:
    choice: str
    model: str
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Recognition:
    text: str
    model: str
    usage: dict = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True)
class Scan:
    fragments: list[str]
    model: str
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    provider: str
    configured: bool
    models: dict[Operation, str]
    asr_streaming: bool = False
