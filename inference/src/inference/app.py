"""Собирает приложение и конкретный адаптер провайдера."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from inference.api.middleware import (
    handle_http_error,
    handle_inference_error,
    handle_validation_error,
    http_observer,
)
from inference.api.routes import create_router
from inference.application.operations import Operations
from inference.config import Settings
from inference.domain.errors import InferenceError
from inference.domain.ports import Provider
from inference.providers.routerai import RouterAIProvider
from inference.runtime.execution import Execution
from inference.runtime.telemetry import configure_logging


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    owned_provider = None

    if provider is None:
        owned_provider = RouterAIProvider(config.routerai)
        provider = owned_provider

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if owned_provider is not None:
                owned_provider.close()

    configure_logging(config.log_level)

    operations = Operations(provider, Execution(config.limits), config.limits)
    app = FastAPI(title="NETI Next Inference", version="1.0.0", lifespan=lifespan)

    app.middleware("http")(
        http_observer(config.limits.max_body_bytes, config.limits.request_timeout_seconds)
    )
    app.add_exception_handler(InferenceError, handle_inference_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.include_router(create_router(operations, config.token.get_secret_value()))

    return app


app = create_app()
