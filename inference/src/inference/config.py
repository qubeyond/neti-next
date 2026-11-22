"""Читает типизированные настройки и секреты инференса из окружения."""

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    Field,
    PositiveInt,
    SecretStr,
    StringConstraints,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CONFIG_DIRECTORY = Path(__file__).resolve().parents[2]
PROMPTS_DIRECTORY = Path(
    os.environ.get("INFERENCE_PROMPTS_DIRECTORY") or CONFIG_DIRECTORY / ".private" / "prompts"
)


def _load_prompt(name: str) -> str:
    try:
        return (PROMPTS_DIRECTORY / name).read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return (CONFIG_DIRECTORY / "prompts" / name).read_text(encoding="utf-8-sig").strip()


class Environment(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        populate_by_name=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
        env_file=(CONFIG_DIRECTORY / ".env.example", CONFIG_DIRECTORY / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


class Limits(Environment):
    model_config = SettingsConfigDict(env_prefix="INFERENCE_")

    request_timeout_seconds: PositiveInt = 85
    max_file_bytes: PositiveInt = 16 * 1024 * 1024
    body_overhead_bytes: PositiveInt = 64 * 1024
    max_messages: PositiveInt = 32
    max_message_chars: PositiveInt = 60_000
    max_total_input_chars: PositiveInt = 120_000
    max_model_chars: PositiveInt = 200
    max_output_tokens: PositiveInt = 16_384
    llm_concurrency: PositiveInt = Field(default=4, validation_alias="INFERENCE_MAX_CONCURRENT")
    asr_concurrency: PositiveInt | None = Field(
        default=None, validation_alias="INFERENCE_ASR_MAX_CONCURRENT"
    )
    ocr_concurrency: PositiveInt | None = Field(
        default=None, validation_alias="INFERENCE_OCR_MAX_CONCURRENT"
    )

    @property
    def max_encoded_bytes(self) -> int:
        return ((self.max_file_bytes + 2) // 3) * 4

    @property
    def max_body_bytes(self) -> int:
        return self.max_encoded_bytes + self.body_overhead_bytes


class RouterAIConfig(Environment):
    api_key: SecretStr = Field(default=SecretStr(""), validation_alias="ROUTERAI_API_KEY")
    base_url: AnyHttpUrl = Field(validation_alias="ROUTERAI_BASE_URL")
    timeout_seconds: PositiveInt = Field(default=70, validation_alias="ROUTERAI_TIMEOUT_SECONDS")
    max_response_bytes: PositiveInt = Field(
        default=4 * 1024 * 1024, validation_alias="ROUTERAI_MAX_RESPONSE_BYTES"
    )
    inference_extra_models: str = ""
    llm_model: NonEmpty
    decision_model: NonEmpty = "~typesafe/jev-latest"
    decision_timeout_seconds: PositiveInt = 3
    asr_model: NonEmpty
    asr_language: NonEmpty
    ocr_model: NonEmpty
    ocr_max_tokens: PositiveInt
    ocr_prompt: NonEmpty = Field(default_factory=lambda: _load_prompt("ocr-prompt.txt"))
    pdf_ocr_engine: NonEmpty
    pdf_ocr_prompt: NonEmpty = Field(default_factory=lambda: _load_prompt("pdf-ocr-prompt.txt"))
    pdf_ocr_max_tokens: PositiveInt

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, address: AnyHttpUrl) -> AnyHttpUrl:
        if (
            address.username is not None
            or address.password is not None
            or address.query
            or address.fragment
        ):
            raise ValueError("Адрес провайдера должен быть без учётных данных, query и fragment.")

        return address


class Settings(Environment):
    model_config = SettingsConfigDict(env_prefix="INFERENCE_")

    limits: Limits = Field(default_factory=Limits)
    routerai: RouterAIConfig = Field(default_factory=lambda: RouterAIConfig.model_validate({}))
    token: SecretStr = SecretStr("")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.strip().upper()
