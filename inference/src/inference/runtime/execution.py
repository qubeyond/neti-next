"""Ограничивает параллельные операции и собирает метрики процесса."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import BoundedSemaphore
from time import perf_counter

from inference.config import Limits
from inference.domain.entities import Completion, Operation, Recognition
from inference.domain.errors import InferenceError
from inference.runtime.metrics import Metrics
from inference.runtime.telemetry import event, numeric_usage


@dataclass(frozen=True)
class Timed[Result: (Completion, Recognition)]:
    value: Result
    latency_ms: int


class Execution:
    def __init__(self, limits: Limits) -> None:
        self.capacity = {
            Operation.LLM: BoundedSemaphore(limits.llm_concurrency),
            Operation.ASR: BoundedSemaphore(
                limits.asr_concurrency or min(limits.llm_concurrency, 2)
            ),
            Operation.OCR: BoundedSemaphore(
                limits.ocr_concurrency or min(limits.llm_concurrency, 2)
            ),
        }
        self.telemetry = Metrics()

    def perform[Result: (Completion, Recognition)](
        self, operation: Operation, run: Callable[[], Result]
    ) -> Timed[Result]:
        capacity = self.capacity[operation]

        if not capacity.acquire(blocking=False):
            self.telemetry.record(operation, "busy")
            self.telemetry.errors.labels(operation, 429).inc()

            event("inference.busy", logging.WARNING, operation=operation, status=429)

            raise InferenceError(429, "Инференс занят.", headers={"Retry-After": "1"})

        self.telemetry.in_flight.labels(operation).inc()

        started = perf_counter()
        status = "failed"
        usage = {}

        try:
            result = run()
            usage = numeric_usage(result.usage)
            latency = round((perf_counter() - started) * 1000)
            status = "success"

            event(
                "inference.completed",
                operation=operation,
                model=result.model,
                latency_ms=latency,
                usage=usage,
            )

            return Timed(result, latency)
        except InferenceError as error:
            self.telemetry.errors.labels(operation, error.status_code).inc()
            event(
                "inference.failed",
                logging.WARNING,
                operation=operation,
                status=error.status_code,
                latency_ms=round((perf_counter() - started) * 1000),
            )

            raise
        finally:
            self.telemetry.in_flight.labels(operation).dec()
            capacity.release()

            self.telemetry.record(operation, status, perf_counter() - started, usage)

    def metrics(self) -> bytes:
        return self.telemetry.export()
