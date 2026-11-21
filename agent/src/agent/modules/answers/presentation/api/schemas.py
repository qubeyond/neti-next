"""Внешний JSON-контракт ответов агента."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints


class QuestionRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]


class Citation(BaseModel):
    url: str
    quote: str


class AnswerResponse(BaseModel):
    status: Literal["answered", "partial", "unverified"]
    answer: str
    citations: list[Citation]


class ErrorResponse(BaseModel):
    detail: str
