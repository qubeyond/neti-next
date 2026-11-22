"""Связывает HTTP-маршруты с операциями инференса."""

from asyncio import create_task
from collections.abc import Callable
from dataclasses import asdict
from secrets import compare_digest
from threading import Event
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.concurrency import run_in_threadpool

from inference.api.problems import responses
from inference.api.schemas import (
    ChatRequest,
    Completion,
    Decision,
    DecisionRequest,
    MediaRequest,
    Recognition,
)
from inference.application.operations import Operations
from inference.runtime.execution import Timed
from inference.runtime.telemetry import DISCONNECTED

BEARER = HTTPBearer(auto_error=False)


def serialize(timed: Timed) -> dict:
    return {**asdict(timed.value), "latency_ms": timed.latency_ms}


async def run_operation(request: Request, operation: Callable[[], Timed]) -> dict:
    disconnected = Event()
    scope = DISCONNECTED.set(disconnected)

    async def watch() -> None:
        if (await request.receive())["type"] == "http.disconnect":
            disconnected.set()

    watcher = create_task(watch())

    try:
        return serialize(await run_in_threadpool(operation))
    finally:
        watcher.cancel()
        DISCONNECTED.reset(scope)


def create_router(operations: Operations, token: str) -> APIRouter:
    router = APIRouter()
    operation_errors = responses(401, 413, 415, 422, 429, 500, 502, 503, 504)

    def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> None:
        supplied = credentials.credentials if credentials is not None else ""

        if token and not compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(
                401, "Требуется доступ сервиса.", headers={"WWW-Authenticate": "Bearer"}
            )

    @router.get("/inference/health")
    def health() -> dict:
        return {"status": "ok"}

    @router.get(
        "/inference/v1/capabilities",
        dependencies=[Depends(authorize)],
        responses=responses(401),
    )
    def capabilities() -> dict:
        return asdict(operations.capabilities())

    @router.get(
        "/inference/metrics",
        dependencies=[Depends(authorize)],
        response_class=PlainTextResponse,
        responses=responses(401),
    )
    def metrics() -> Response:
        return Response(operations.metrics(), media_type=CONTENT_TYPE_LATEST)

    @router.post(
        "/inference/v1/generate",
        dependencies=[Depends(authorize)],
        response_model=Completion,
        responses=operation_errors,
    )
    async def generate(request: Request, payload: ChatRequest) -> dict:
        return await run_operation(request, lambda: operations.generate(payload.to_domain()))

    @router.post(
        "/inference/v1/decide",
        dependencies=[Depends(authorize)],
        response_model=Decision,
        responses=operation_errors,
    )
    async def decide(request: Request, payload: DecisionRequest) -> dict:
        return await run_operation(request, lambda: operations.decide(payload.to_domain()))

    @router.post(
        "/inference/v1/transcribe",
        dependencies=[Depends(authorize)],
        response_model=Recognition,
    )
    async def speech(request: Request, media: MediaRequest) -> dict:
        return await run_operation(request, lambda: operations.transcribe(media.data, media.mime))

    @router.post(
        "/inference/v1/ocr",
        dependencies=[Depends(authorize)],
        response_model=Recognition,
        responses=operation_errors,
    )
    async def ocr(request: Request, media: MediaRequest) -> dict:
        return await run_operation(request, lambda: operations.recognize(media.data, media.mime))

    return router
