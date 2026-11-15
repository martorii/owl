"""Settings → pydantic_ai model (CONTRACT §10). The only module that knows the providers."""

from dataclasses import dataclass
from typing import Any

from pydantic_ai import NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
from pydantic_ai.settings import ModelSettings as GenerationSettings

from owl.infrastructure.settings import (
    ModelRole,
    ModelSettings,
    ModelsSettings,
    OutputMode,
    Provider,
)

# Recorded as the provider name of responses from an OpenAI-compatible server (LM Studio locally).
OPENAI_COMPATIBLE_SERVICE = "openai-compatible"


@dataclass(frozen=True)
class ConfiguredModel:
    """A built model with the generation settings and output mode of its role."""

    model: Model
    model_settings: GenerationSettings
    output_mode: OutputMode

    def output_spec[T](
        self, output_type: type[T]
    ) -> NativeOutput[T] | ToolOutput[T] | PromptedOutput[T]:
        """Wrap ``output_type`` for an agent according to ``OUTPUT_MODE``."""
        match self.output_mode:
            case OutputMode.NATIVE:
                return NativeOutput(output_type)
            case OutputMode.TOOL:
                return ToolOutput(output_type)
            case OutputMode.PROMPTED:
                return PromptedOutput(output_type)


class ModelFactory:
    """Builds the model of a role. Every provider uses the Chat Completions API.

    The OpenAI SDK's own retries are switched off (``max_retries: 0``): rate-limit and transient
    server errors are retried by the rate-limit layer, within ``RATE_LIMIT_MAX_WAIT`` (§9.5).
    Building makes no request.
    """

    def __init__(self, models: ModelsSettings) -> None:
        self._models = models

    def build(self, role: ModelRole) -> ConfiguredModel:
        settings = self._models.model_for(role)
        generation: GenerationSettings = {
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "timeout": settings.timeout,
            "max_retries": 0,
        }
        return ConfiguredModel(_model(settings), generation, settings.output_mode)


def _model(settings: ModelSettings) -> Model:
    key = settings.api_key.get_secret_value() if settings.api_key else None
    provider: Any
    match settings.provider:
        case Provider.OPENAI_COMPATIBLE:
            provider = OpenAICompatibleProvider(
                OPENAI_COMPATIBLE_SERVICE, base_url=_required(settings.base_url), api_key=key
            )
            name = _required(settings.name)
        case Provider.AZURE_OPENAI:
            provider = AzureProvider(
                azure_endpoint=settings.azure_endpoint,
                api_version=settings.api_version,
                api_key=key,
            )
            name = _required(settings.deployment)
        case Provider.OPENAI:
            provider = OpenAIProvider(base_url=settings.base_url, api_key=key)
            name = _required(settings.name)
    return OpenAIChatModel(name, provider=provider)


def _required(value: str | None) -> str:
    if value is None:  # ModelSettings validates the provider's required keys
        raise AssertionError("required model setting missing")
    return value
