"""Регистрирует счётчики и распределение задержек в Prometheus."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from inference.domain.entities import Operation
from inference.runtime.telemetry import USAGE_FIELDS


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.outcomes = {
            outcome: Counter(
                f"neti_inference_{outcome}_total",
                f"Inference operations with outcome {outcome}.",
                ("operation",),
                registry=self.registry,
            )
            for outcome in ("success", "failed", "busy")
        }
        self.in_flight = Gauge(
            "neti_inference_in_flight",
            "Active provider calls.",
            ("operation",),
            registry=self.registry,
        )
        self.errors = Counter(
            "neti_inference_http_error_total",
            "Inference errors by HTTP status.",
            ("operation", "status"),
            registry=self.registry,
        )
        self.usage = {
            key: Counter(
                f"neti_inference_{key}_total",
                f"Provider usage field {key}.",
                ("operation",),
                registry=self.registry,
            )
            for key in USAGE_FIELDS
        }
        self.duration = Histogram(
            "neti_inference_duration_seconds",
            "Time spent executing inference operations.",
            ("operation",),
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 40, 60, 90, 120),
            registry=self.registry,
        )

    def record(
        self,
        operation: Operation,
        outcome: str,
        elapsed: float | None = None,
        usage: dict[str, int | float] | None = None,
    ) -> None:
        self.outcomes[outcome].labels(operation).inc()

        if elapsed is not None:
            self.duration.labels(operation).observe(elapsed)

        for key, value in (usage or {}).items():
            self.usage[key].labels(operation).inc(value)

    def export(self) -> bytes:
        return generate_latest(self.registry)
