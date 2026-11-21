"""Собирает HTTP-приложение агента."""

from collections.abc import Callable

from fastapi import FastAPI

from .composition import answer_question
from .modules.answers.presentation.api.router import create_router
from .modules.health.router import router as health_router


def create_app(answer: Callable[[str], dict] = answer_question) -> FastAPI:
    app = FastAPI(
        title="NETI Next Agent",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.include_router(health_router)
    app.include_router(create_router(answer))

    return app


app = create_app()
