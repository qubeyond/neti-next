import os
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, StringConstraints

from .config import AgentError
from .service import answer_question

app = FastAPI(
    title="NETI Next Agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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


@app.get("/agent/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/v1/answers")
def create_answer(request: QuestionRequest) -> AnswerResponse:
    if os.getenv("WEB_ANSWERS_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "Веб-ответы выключены в этом окружении.")
    try:
        result = answer_question(request.question)
    except AgentError as error:
        raise HTTPException(
            502, "Не удалось получить ответ от внешнего сервиса."
        ) from error
    if result["status"] == "invalid":
        raise HTTPException(502, "Внешний сервис вернул ответ в неверном формате.")
    return AnswerResponse(
        status=result["status"],
        answer=result["answer"],
        citations=[
            Citation(url=item["url"], quote=item["quote"])
            for item in result["citations"]
            if item["verified"]
        ],
    )
