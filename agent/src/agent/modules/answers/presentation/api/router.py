"""Принимает вопросы от других сервисов по HTTP."""

import os
from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from agent.shared.errors import AgentError

from .schemas import AnswerResponse, Citation, ErrorResponse, QuestionRequest


def create_router(answer_question: Callable[[str], dict]) -> APIRouter:
    router = APIRouter(prefix="/agent/v1", tags=["answers"])

    @router.post(
        "/answers",
        operation_id="createAgentAnswer",
        responses={
            502: {
                "model": ErrorResponse,
                "description": "Ошибка провайдера или формата ответа",
            },
            503: {"model": ErrorResponse, "description": "Веб-ответы выключены"},
        },
    )
    def create_answer(request: QuestionRequest) -> AnswerResponse:
        if os.getenv("WEB_ANSWERS_ENABLED", "false").lower() != "true":
            raise HTTPException(503, "Веб-ответы выключены в этом окружении.")

        try:
            result = answer_question(request.question)
        except AgentError as error:
            raise HTTPException(502, "Не удалось получить ответ от внешнего сервиса.") from error

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

    return router
