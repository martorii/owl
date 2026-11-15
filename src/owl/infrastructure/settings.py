"""Application settings (CONTRACT §4, §7.1, §8.2, §9.5, §10).

Environment variables (and an optional ``.env`` file) with prefix ``OWL_`` and nested delimiter
``__``, e.g. ``OWL_MODELS__EXTRACTION__PROVIDER``. Models are configured per role; a role that
is not configured at all falls back to ``default`` as a whole (never key by key), and a role
that is configured must be complete.
"""

import math
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from owl.domain.evidence import MatcherConfig
from owl.domain.values import DateOrder, ParseOptions


class SettingsError(ValueError):
    """The settings cannot provide what was asked for."""


class Provider(StrEnum):
    OPENAI_COMPATIBLE = "openai_compatible"
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"


class OutputMode(StrEnum):
    NATIVE = "native"
    TOOL = "tool"
    PROMPTED = "prompted"


class ModelRole(StrEnum):
    EXTRACTION = "extraction"
    POLISHING = "polishing"
    JUDGE = "judge"


_REQUIRED: dict[Provider, tuple[str, ...]] = {
    Provider.OPENAI_COMPATIBLE: ("name", "base_url"),
    Provider.OPENAI: ("name", "api_key"),
    Provider.AZURE_OPENAI: ("deployment", "azure_endpoint", "api_version", "api_key"),
}


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelSettings(_Section):
    provider: Provider
    name: str | None = None
    deployment: str | None = None  # azure_openai
    base_url: str | None = None  # openai_compatible (optional override for openai)
    azure_endpoint: str | None = None
    api_version: str | None = None
    api_key: SecretStr | None = None
    context_window: PositiveInt
    temperature: float = Field(default=0.0, ge=0)
    max_tokens: PositiveInt = 4096
    timeout: float = Field(default=120.0, gt=0)  # seconds per LLM call
    max_retries: int = Field(default=2, ge=0)  # ModelRetry budget (CONTRACT §8.4)
    output_mode: OutputMode = OutputMode.NATIVE
    tpm_limit: PositiveInt | None = None  # CONTRACT §9.5; unset = no pacing
    rate_limit_max_wait: float = Field(default=600.0, ge=0)  # seconds of 429 waiting per call

    @model_validator(mode="after")
    def _provider_keys(self) -> Self:
        missing = [key for key in _REQUIRED[self.provider] if getattr(self, key) is None]
        if missing:
            raise ValueError(f"provider {self.provider.value} requires {', '.join(missing)}")
        return self


class ModelsSettings(_Section):
    default: ModelSettings | None = None
    extraction: ModelSettings | None = None
    polishing: ModelSettings | None = None
    judge: ModelSettings | None = None

    @model_validator(mode="after")
    def _extraction_resolves(self) -> Self:
        if self.extraction is None and self.default is None:
            raise ValueError("no model for role extraction: configure extraction or default")
        return self

    def model_for(self, role: ModelRole) -> ModelSettings:
        configured: ModelSettings | None = getattr(self, role.value)
        model = configured or self.default
        if model is None:
            raise SettingsError(f"no model for role {role.value}: configure it or default")
        return model


class ParsingSettings(_Section):
    date_order: DateOrder = DateOrder.DAY_FIRST
    decimal_places: int = Field(default=6, ge=0)
    enum_similarity_threshold: float = Field(default=0.92, gt=0, le=1)
    enum_similarity_margin: float = Field(default=0.03, ge=0, le=1)

    def to_parse_options(self) -> ParseOptions:
        return ParseOptions(
            self.date_order,
            self.decimal_places,
            self.enum_similarity_threshold,
            self.enum_similarity_margin,
        )


class MatcherSettings(_Section):
    fuzzy_min_length: int = Field(default=25, ge=1)
    fuzzy_threshold: float = Field(default=92.0, gt=0, le=100)

    def to_matcher_config(self) -> MatcherConfig:
        return MatcherConfig(self.fuzzy_min_length, self.fuzzy_threshold)


class ContextSettings(_Section):
    budget_fraction: float = Field(default=0.6, gt=0, lt=1)  # of CONTEXT_WINDOW, document text


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OWL_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    models: ModelsSettings = Field(default={}, validate_default=True)
    parsing: ParsingSettings = Field(default_factory=ParsingSettings)
    matcher: MatcherSettings = Field(default_factory=MatcherSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)

    def context_budget(self, role: ModelRole = ModelRole.EXTRACTION) -> int:
        """Tokens available for document text in one call of ``role`` (CONTRACT §7.1)."""
        window = self.models.model_for(role).context_window
        return math.floor(window * self.context.budget_fraction)


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Read settings from the environment and ``env_file`` (``None``: environment only)."""
    return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
