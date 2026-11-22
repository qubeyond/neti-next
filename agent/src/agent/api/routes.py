"""Принимает вопросы от других сервисов по HTTP."""

import json
import re
from asyncio import Queue, create_task, get_running_loop, wait_for
from collections.abc import AsyncIterator, Callable
from secrets import compare_digest
from threading import BoundedSemaphore, Event
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from agent.api.problems import responses
from agent.api.schemas import AnswerResponse, CalendarEvent, Citation, QuestionRequest
from agent.config import flag, load_settings, optional, positive_int
from agent.domain.errors import AgentError
from agent.runtime import DISCONNECTED
from agent.trace import TRACE

_NDJSON = "application/x-ndjson"
_ACTIVITY_OPERATIONS = {
    "resolve_context",
    "route_turn",
    "search",
    "backup_search",
    "read",
    "select_skills",
    "generation",
    "regeneration",
    "verify",
}
_ACTIVITY_FIELDS = {
    "type",
    "id",
    "operation",
    "duration_ms",
    "outcome",
    "provider",
    "domains",
    "count",
    "active_skills",
    "sources",
}


def _admit(authorization: str, capacity: BoundedSemaphore, *, demo: bool = False) -> None:
    settings = load_settings()
    token = optional(settings, "AGENT_TOKEN")
    scheme, _, supplied = authorization.partition(" ")

    if (demo and not token) or (
        token
        and (scheme.lower() != "bearer" or not compare_digest(supplied.encode(), token.encode()))
    ):
        raise HTTPException(
            401, "Требуется доступ сервиса.", headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        enabled = flag(settings, "WEB_ANSWERS_ENABLED")
    except AgentError as error:
        raise HTTPException(503, "Веб-ответы не настроены.") from error

    if not enabled:
        raise HTTPException(503, "Веб-ответы выключены в этом окружении.")

    if not capacity.acquire(blocking=False):
        raise HTTPException(429, "Агент занят.", headers={"Retry-After": "1"})


def _http_error(error: AgentError) -> HTTPException:
    detail = {
        429: "Лимит провайдера исчерпан.",
        503: "Сервис временно недоступен.",
        504: "Бюджет времени исчерпан.",
    }.get(error.status, "Не удалось получить ответ от внешнего сервиса.")

    return HTTPException(error.status, detail)


def _answer_response(result: dict) -> AnswerResponse:
    if result["status"] == "invalid":
        raise HTTPException(502, "Внешний сервис вернул ответ в неверном формате.")

    events = (
        [CalendarEvent(**item) for item in result.get("events", [])]
        if result["status"] in {"answered", "partial"}
        else []
    )
    markers = re.findall(r"〔([1-9][0-9]{0,2})〕|\[([1-9][0-9]{0,2})\](?![([])", result["answer"])
    used_refs = {int(numbered or legacy) for numbered, legacy in markers}

    used_refs.update(item.citation for item in events)

    blocks = result.get("blocks", []) if result["status"] in {"answered", "partial"} else []
    suggestions = []

    for block in blocks:
        used_refs.update(operand["ref"] for operand in block.get("operands", []))
        used_refs.update(cell["ref"] for row in block.get("rows", []) for cell in row if cell)

    if result["status"] == "clarification":
        wording = result["answer"].casefold()

        if "какую информацию" in wording:
            suggestions = ["Поступление", "Расписание", "Общежитие"]
        elif "уровень" in wording or "бакалавр" in wording:
            suggestions = ["Бакалавриат", "Магистратура", "Специалитет"]
    elif result["status"] == "unverified" and "blocks" not in result:
        blocks = [
            {
                "kind": "gap",
                "title": "Нужно уточнить",
                "note": "Пришлите официальную страницу или документ с нужным условием.",
            }
        ]

    return AnswerResponse.model_validate(
        dict(
            status=result["status"],
            answer=result["answer"],
            citations=[
                Citation(
                    url=item["url"],
                    quote=item.get("display_quote") or item["quote"],
                    ref=item["ref"] if item.get("ref") in used_refs else None,
                    attachment_id=item.get("attachment_id"),
                )
                for item in result["citations"]
                if item["verified"]
            ],
            events=events,
            blocks=blocks,
            suggestions=suggestions,
        )
    )


def create_router(answer_question: Callable[..., dict]) -> APIRouter:
    router = APIRouter(prefix="/agent")
    capacity = BoundedSemaphore(positive_int(load_settings(), "AGENT_MAX_CONCURRENT"))

    async def answer(request: QuestionRequest) -> dict:
        options = request.model_dump(exclude_defaults=True, exclude={"question"})

        try:
            return await run_in_threadpool(answer_question, request.question, **options)
        except AgentError as error:
            raise _http_error(error) from error

    @router.get("/health", operation_id="getAgentHealth", tags=["health"])
    def health() -> dict[str, Literal["ok"]]:
        return {"status": "ok"}

    @router.post(
        "/v1/answers",
        tags=["answers"],
        operation_id="createAgentAnswer",
        response_model=AnswerResponse,
        response_model_exclude_defaults=True,
        responses={
            200: {"content": {_NDJSON: {"schema": {"type": "string"}}}},
            **responses(401, 411, 413, 422, 429, 500, 502, 503, 504),
        },
    )
    async def create_answer(
        request: QuestionRequest, http_request: Request, authorization: str = Header(default="")
    ) -> AnswerResponse | StreamingResponse:
        _admit(authorization, capacity)

        if _NDJSON in http_request.headers.get("accept", ""):
            return StreamingResponse(
                events(request, activity_only=True),
                media_type=_NDJSON,
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )

        disconnected = Event()
        scope = DISCONNECTED.set(disconnected)

        async def watch() -> None:
            if (await http_request.receive())["type"] == "http.disconnect":
                disconnected.set()

        watcher = create_task(watch())

        try:
            return _answer_response(await answer(request))
        finally:
            watcher.cancel()
            DISCONNECTED.reset(scope)
            capacity.release()

    async def events(
        request: QuestionRequest, *, activity_only: bool = False
    ) -> AsyncIterator[str]:
        queue: Queue[dict] = Queue()
        loop = get_running_loop()
        disconnected = Event()

        activity_spans: set[str] = set()

        def emit(event: dict) -> None:
            if disconnected.is_set():
                return

            if activity_only:
                kind = event["type"]

                if kind == "span_start" and event.get("operation") in _ACTIVITY_OPERATIONS:
                    activity_spans.add(event["id"])
                elif kind == "span_end" and event["id"] in activity_spans:
                    activity_spans.remove(event["id"])
                elif kind in {"span_start", "span_end"}:
                    return

                if kind in {"span_start", "span_end"}:
                    event = {key: value for key, value in event.items() if key in _ACTIVITY_FIELDS}
                elif kind == "answer":
                    event = {"type": kind, "answer": event["answer"]}

            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def run() -> None:
            scope = DISCONNECTED.set(disconnected)
            trace = TRACE.set(emit)

            try:
                result = await answer(request)
                reply = _answer_response(result).model_dump(exclude_defaults=True)
                decision = result.get("grounding_failure")
                recovery = (result.get("recovery") or {}).get("outcome")

                emit(
                    {"type": "answer", "answer": reply, "decision": decision, "recovery": recovery}
                )
            except HTTPException as error:
                emit({"type": "error", "http_status": error.status_code})
            except Exception:
                emit({"type": "error", "http_status": 500})
            finally:
                TRACE.reset(trace)
                DISCONNECTED.reset(scope)
                capacity.release()

        run_task = create_task(run())

        try:
            while True:
                try:
                    item = await wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    item = {"type": "heartbeat"}

                yield json.dumps(item, ensure_ascii=False) + "\n"

                if item["type"] in {"answer", "error"}:
                    break
        finally:
            disconnected.set()

            if run_task.done():
                await run_task

    @router.post(
        "/v1/answers/trace",
        tags=["answers"],
        operation_id="traceAgentAnswer",
        response_class=StreamingResponse,
        responses={
            200: {"content": {_NDJSON: {"schema": {"type": "string"}}}},
            **responses(401, 411, 413, 422, 429, 500, 503, 504),
        },
    )
    async def trace_answer(
        request: QuestionRequest, authorization: str = Header(default="")
    ) -> StreamingResponse:
        _admit(authorization, capacity, demo=True)

        return StreamingResponse(
            events(request),
            media_type=_NDJSON,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
