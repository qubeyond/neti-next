"""Собирает HTTP-приложение агента."""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from time import monotonic, perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from agent.api.problems import problem
from agent.api.routes import create_router
from agent.composition import answer_question
from agent.config import load_settings, positive_int
from agent.providers.document_cache import close_document_cache
from agent.runtime import DEADLINE, REQUEST_ID, deadline, request_id

MAX_BODY_BYTES = 64 * 1024
logger = logging.getLogger("uvicorn.error.agent")


def create_app(answer: Callable[..., dict] = answer_question) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            close_document_cache()

    app = FastAPI(
        title="NETI Next Agent",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.include_router(create_router(answer))

    @app.middleware("http")
    async def observe(request: Request, call_next: RequestResponseEndpoint) -> Response:
        identifier = request_id(request.headers.get("X-Request-ID", ""))
        request.state.request_id = identifier
        id_token = REQUEST_ID.set(identifier)
        started = perf_counter()
        deadline_token = None

        try:
            if request.method == "POST":
                maximum = positive_int(load_settings(), "AGENT_TURN_TIMEOUT_SECONDS")
                end = deadline(request.headers.get("X-Deadline-Ms", ""), maximum)
                deadline_token = DEADLINE.set(end)
                length = request.headers.get("content-length", "")

                if not length.isascii() or not length.isdecimal() or len(length) > 8:
                    response = problem(request, 411, "Укажите размер запроса.")
                elif int(length) > MAX_BODY_BYTES:
                    response = problem(request, 413, "Размер запроса превышает лимит.")
                elif end <= monotonic():
                    response = problem(request, 504, "Бюджет времени исчерпан.")
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        except Exception as error:
            logger.error("agent.failed request_id=%s error=%s", identifier, type(error).__name__)
            response = problem(request, 500, "Не удалось обработать запрос.")
        finally:
            if deadline_token is not None:
                DEADLINE.reset(deadline_token)

            REQUEST_ID.reset(id_token)

        response.headers["X-Request-ID"] = identifier

        if request.method == "POST":
            logger.info(
                "agent.request request_id=%s status=%s latency_ms=%s",
                identifier,
                response.status_code,
                round((perf_counter() - started) * 1000),
            )

        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: Exception) -> Response:
        if not isinstance(error, HTTPException):
            raise error

        return problem(request, error.status_code, str(error.detail), headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: Exception) -> Response:
        if not isinstance(error, RequestValidationError):
            raise error

        errors = [{"loc": issue["loc"], "type": issue["type"]} for issue in error.errors()]

        return problem(request, 422, "Проверьте поля запроса.", errors=errors)

    return app


app = create_app()
