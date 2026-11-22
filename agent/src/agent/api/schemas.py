"""Внешний JSON-контракт ответов агента."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

HistoryText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=800)]


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: HistoryText


class ImageSource(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{48}$")]
    text: Annotated[str, StringConstraints(min_length=1, max_length=6000)]


class QuestionRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    history: list[HistoryTurn] = Field(default_factory=list, max_length=16)
    quoted_text: Annotated[str, StringConstraints(max_length=1200)] = ""
    attachment_text: Annotated[str, StringConstraints(max_length=6000)] = ""
    image_source: ImageSource | None = None


class Citation(BaseModel):
    url: str
    quote: str
    ref: int | None = None
    attachment_id: str | None = None


class CalendarEvent(BaseModel):
    date: str
    title: str
    citation: int


class BlockCell(BaseModel):
    value: str
    ref: int


class ChatGrid(BaseModel):
    kind: Literal["table", "office", "edition"]
    title: str
    columns: list[str]
    rows: list[list[BlockCell | None]]


class ChatCalculation(BaseModel):
    kind: Literal["calculation"]
    title: str
    operator: Literal["+", "-", "*", "/"]
    operands: tuple[BlockCell, BlockCell]
    result: str


class ChatGap(BaseModel):
    kind: Literal["gap"]
    title: str
    note: str


class AnswerResponse(BaseModel):
    status: Literal["answered", "partial", "unverified", "clarification"]
    answer: str
    citations: list[Citation]
    events: list[CalendarEvent] = Field(default_factory=list)
    blocks: list[ChatGrid | ChatCalculation | ChatGap] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
