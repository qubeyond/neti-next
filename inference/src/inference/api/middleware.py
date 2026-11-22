"""Ограничивает размер HTTP-запроса и связывает его с логами."""

import logging
import re
from collections.abc import Awaitable, Callable
from time import monotonic, perf_counter, time
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from inference.api.problems import problem
from inference.domain.errors import InferenceError
from inference.runtime.telemetry import DEADLINE, REQUEST_ID, event

_TRACE_ID = re.compile(r"[0-9a-f]{32}")


def http_observer(max_body_bytes: int, timeout_seconds: int):
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _TRACE_ID.fullmatch(supplied) else uuid4().hex
        request.state.request_id = request_id
        context = REQUEST_ID.set(request_id)
        started = perf_counter()
        status = 500
        remaining = timeout_seconds
        limit = request.headers.get("X-Deadline-Ms", "")

        if len(limit) <= 13 and limit.isascii() and limit.isdecimal():
            remaining = min(timeout_seconds, int(limit) / 1000 - time())

        deadline = DEADLINE.set(monotonic() + remaining)

        try:
            try:
                if request.method == "POST" and not valid_body_size(request, max_body_bytes):
                    response = problem(request, 413, "Недопустимый размер запроса.")
                elif request.method == "POST" and remaining <= 0:
                    response = problem(request, 504, "Бюджет времени исчерпан.")
                else:
                    response = await call_next(request)
            except Exception as error:
                event("inference.unexpected", logging.ERROR, error=type(error).__name__)
                response = problem(request, 500, "Не удалось обработать запрос.")

            response.headers["X-Request-ID"] = request_id
            status = response.status_code

            return response
        finally:
            try:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                level = logging.WARNING if status >= 400 else logging.INFO

                if status >= 400 or route not in {"/inference/health", "/inference/metrics"}:
                    event(
                        "http.request",
                        level,
                        method=request.method,
                        route=route,
                        status=status,
                        latency_ms=round((perf_counter() - started) * 1000),
                    )
            finally:
                DEADLINE.reset(deadline)
                REQUEST_ID.reset(context)

    return observe


def valid_body_size(request: Request, maximum: int) -> bool:
    size = request.headers.get("content-length", "")
    return size.isascii() and size.isdecimal() and len(size) <= 10 and 0 < int(size) <= maximum


async def handle_inference_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, InferenceError):
        raise error

    return problem(request, error.status_code, error.detail, headers=error.headers)


async def handle_validation_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, RequestValidationError):
        raise error

    errors = [{"loc": item["loc"], "type": item["type"]} for item in error.errors()]

    return problem(request, 422, "Проверьте поля запроса.", errors=errors)


async def handle_http_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, HTTPException):
        raise error

    return problem(request, error.status_code, str(error.detail), headers=error.headers)
