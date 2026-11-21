"""Оформляет ошибки агента и связывает их с запросом."""

from http import HTTPStatus

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

PROBLEM_MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    request_id: str
    errors: list[dict] | None = None


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_MEDIA_TYPE


def problem(request: Request, status: int, detail: str, *, headers=None, errors=None):
    title = HTTPStatus(status).phrase
    code = title.lower().replace(" ", "-")
    data = Problem(
        type=f"urn:neti:problem:{code}",
        title=title,
        status=status,
        detail=detail,
        request_id=request.state.request_id,
        errors=errors,
    )
    response_headers = {**(headers or {}), "Cache-Control": "no-store"}

    return ProblemResponse(data.model_dump(exclude_none=True), status, headers=response_headers)


def responses(*statuses: int) -> dict:
    return {
        status: {
            "description": HTTPStatus(status).phrase,
            "content": {PROBLEM_MEDIA_TYPE: {"schema": Problem.model_json_schema()}},
        }
        for status in statuses
    }
