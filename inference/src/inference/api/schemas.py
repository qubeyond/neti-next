"""Определяет HTTP-схемы запросов и ответов инференса."""

from typing import Literal

from pydantic import BaseModel, Field

from inference.domain.entities import Generation, Message, Role


class ChatMessage(BaseModel):
    role: Role
    content: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    response_format: dict | None = None
    reasoning: dict | None = None

    def to_domain(self) -> Generation:
        return Generation(
            messages=[Message(role=item.role, content=item.content) for item in self.messages],
            max_tokens=self.max_tokens,
            model=self.model,
            response_format=self.response_format,
            reasoning=self.reasoning,
        )


class MediaRequest(BaseModel):
    data: str = Field(min_length=4)
    mime: Literal[
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
        "audio/webm",
        "audio/ogg",
        "audio/wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/flac",
    ]


class Result(BaseModel):
    model: str
    usage: dict
    latency_ms: int


class Completion(Result):
    answer: str
    annotations: list
    finish_reason: str | None


class Recognition(Result):
    text: str
    truncated: bool = False
