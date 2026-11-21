"""Проверка доступности HTTP без вызовов провайдеров."""

from typing import Literal

from fastapi import APIRouter

router = APIRouter(prefix="/agent", tags=["health"])


@router.get("/health", operation_id="getAgentHealth")
def health() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}
