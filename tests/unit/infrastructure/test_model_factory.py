import ast
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput, models
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

from owl.infrastructure.llm.model_factory import ModelFactory
from owl.infrastructure.settings import ModelRole, ModelSettings, ModelsSettings

SRC = Path(__file__).resolve().parents[3] / "src" / "owl"
FACTORY = SRC / "infrastructure" / "llm" / "model_factory.py"
PROVIDER_SPECIFIC = ("openai", "pydantic_ai.providers", "pydantic_ai.models.openai")


def local(**overrides: object) -> ModelSettings:
    raw: dict[str, object] = {
        "provider": "openai_compatible",
        "name": "local-model",
        "base_url": "http://localhost:1234/v1",
        "api_key": "lm-studio",
        "context_window": 32768,
    }
    return ModelSettings.model_validate(raw | overrides)


AZURE = ModelSettings.model_validate(
    {
        "provider": "azure_openai",
        "deployment": "gpt-deploy",
        "azure_endpoint": "https://x.openai.azure.com",
        "api_version": "2024-10-21",
        "api_key": "azure-secret",
        "context_window": 128000,
    }
)
OPENAI = ModelSettings.model_validate(
    {"provider": "openai", "name": "gpt-x", "api_key": "sk-secret", "context_window": 128000}
)


def build(model: ModelSettings, role: ModelRole = ModelRole.EXTRACTION):
    return ModelFactory(ModelsSettings(default=model)).build(role)


def test_openai_compatible() -> None:
    built = build(local())
    assert isinstance(built.model, OpenAIChatModel)
    assert built.model.model_name == "local-model"
    provider = built.model._provider  # pyright: ignore[reportPrivateUsage]
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.base_url == "http://localhost:1234/v1/"
    assert provider.client.api_key == "lm-studio"


def test_openai_compatible_without_api_key() -> None:
    built = build(local(api_key=None))
    assert isinstance(built.model, OpenAIChatModel)


def test_azure_uses_the_deployment_as_model_name() -> None:
    built = build(AZURE)
    assert isinstance(built.model, OpenAIChatModel)
    assert built.model.model_name == "gpt-deploy"
    provider = built.model._provider  # pyright: ignore[reportPrivateUsage]
    assert isinstance(provider, AzureProvider)
    assert provider.base_url.startswith("https://x.openai.azure.com")
    assert provider.client.api_key == "azure-secret"


def test_openai_default_and_overridden_base_url() -> None:
    built = build(OPENAI)
    provider = built.model._provider  # pyright: ignore[reportPrivateUsage]
    assert isinstance(provider, OpenAIProvider)
    assert built.model.model_name == "gpt-x"
    assert provider.base_url == "https://api.openai.com/v1/"
    assert provider.client.api_key == "sk-secret"

    proxied = build(OPENAI.model_copy(update={"base_url": "https://proxy.example/v1"}))
    assert proxied.model._provider.base_url == "https://proxy.example/v1/"  # pyright: ignore[reportPrivateUsage]


def test_generation_settings_and_sdk_retries_off() -> None:
    built = build(local(temperature=0.2, max_tokens=1000, timeout=30))
    assert built.model_settings == {
        "temperature": 0.2,
        "max_tokens": 1000,
        "timeout": 30.0,
        "max_retries": 0,
    }


def test_roles_resolve_with_fallback() -> None:
    factory = ModelFactory(ModelsSettings(default=local(), extraction=OPENAI))
    assert factory.build(ModelRole.EXTRACTION).model.model_name == "gpt-x"
    assert factory.build(ModelRole.JUDGE).model.model_name == "local-model"


class Out(BaseModel):
    value: str


@pytest.mark.parametrize(
    ("mode", "wrapper"),
    [("native", NativeOutput), ("tool", ToolOutput), ("prompted", PromptedOutput)],
)
def test_output_mode_switch(mode: str, wrapper: type) -> None:
    built = build(local(output_mode=mode))
    assert isinstance(built.output_spec(Out), wrapper)


def test_building_makes_no_request() -> None:
    assert models.ALLOW_MODEL_REQUESTS is False
    built = build(local())
    agent = Agent(built.model, output_type=built.output_spec(Out))
    with pytest.raises(RuntimeError, match="ALLOW_MODEL_REQUESTS"):
        agent.run_sync("hi", model_settings=built.model_settings)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_no_provider_specific_import_outside_the_factory() -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(
            name
            for name in _imports(path)
            if any(name == p or name.startswith(p + ".") for p in PROVIDER_SPECIFIC)
        )
        for path in SRC.rglob("*.py")
        if path != FACTORY
    }
    assert {k: v for k, v in offenders.items() if v} == {}
    assert _imports(FACTORY) & {"pydantic_ai.models.openai"}  # the scan does see the factory
