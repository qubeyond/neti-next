"""Определяет HTTP-схемы запросов и ответов инференса."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from inference.domain.entities import ChoiceRequest, Generation, Message, Role


class ChatMessage(BaseModel):
    role: Role
    content: str


class FunctionDefinition(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str = Field(default="", max_length=2000)
    parameters: dict


class FunctionTool(BaseModel):
    type: Literal["function"]
    function: FunctionDefinition


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    response_format: dict | None = None
    reasoning: dict | None = None
    tools: list[FunctionTool] | None = Field(default=None, min_length=1, max_length=8)
    tool_choice: Literal["auto", "none"] | None = None

    def to_domain(self) -> Generation:
        return Generation(
            messages=[Message(role=item.role, content=item.content) for item in self.messages],
            max_tokens=self.max_tokens,
            model=self.model,
            response_format=self.response_format,
            reasoning=self.reasoning,
            tools=[tool.model_dump() for tool in self.tools] if self.tools else None,
            tool_choice=self.tool_choice if self.tools else None,
        )


class DecisionRequest(BaseModel):
    state: str = Field(min_length=1, max_length=12000)
    instructions: str = Field(min_length=1, max_length=3000)
    choices: dict[
        Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")],
        Annotated[str, StringConstraints(min_length=1, max_length=500)],
    ] = Field(min_length=2, max_length=8)

    def to_domain(self) -> ChoiceRequest:
        return ChoiceRequest(self.state, self.instructions, self.choices)


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
    tool_calls: list[dict] = Field(default_factory=list)


class Decision(Result):
    choice: str


class Recognition(Result):
    text: str
    truncated: bool = False
