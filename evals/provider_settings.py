"""Читает ключ RouterAI для экспериментов с API моделей принятия решений."""

from pathlib import Path

from agent.config import load_settings, optional, required

INFERENCE_ENV = Path(__file__).resolve().parents[1] / "inference" / ".env"


def routerai_key(settings: dict[str, str]) -> str:
    return optional(settings, "ROUTERAI_API_KEY") or required(
        load_settings(INFERENCE_ENV), "ROUTERAI_API_KEY"
    )
