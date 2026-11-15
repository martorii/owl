import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from owl.domain.evidence import MatcherConfig
from owl.domain.values import DateOrder, ParseOptions
from owl.infrastructure.settings import (
    ModelRole,
    OutputMode,
    Provider,
    Settings,
    SettingsError,
    load_settings,
)

LOCAL = {
    "OWL_MODELS__DEFAULT__PROVIDER": "openai_compatible",
    "OWL_MODELS__DEFAULT__NAME": "local-model",
    "OWL_MODELS__DEFAULT__BASE_URL": "http://localhost:1234/v1",
    "OWL_MODELS__DEFAULT__API_KEY": "lm-studio-secret",
    "OWL_MODELS__DEFAULT__CONTEXT_WINDOW": "32768",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.upper().startswith("OWL_"):
            monkeypatch.delenv(key)


def load(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings(env_file=None)


def test_nested_env_parsing_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, LOCAL)
    model = settings.models.model_for(ModelRole.EXTRACTION)
    assert model.provider is Provider.OPENAI_COMPATIBLE
    assert model.name == "local-model"
    assert model.base_url == "http://localhost:1234/v1"
    assert model.context_window == 32768
    assert model.temperature == 0
    assert model.max_tokens == 4096
    assert model.timeout == 120
    assert model.max_retries == 2
    assert model.output_mode is OutputMode.NATIVE
    assert model.tpm_limit is None
    assert model.rate_limit_max_wait == 600
    assert settings.context.budget_fraction == 0.6


def test_role_without_settings_falls_back_to_default_as_a_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load(
        monkeypatch,
        LOCAL
        | {
            "OWL_MODELS__EXTRACTION__PROVIDER": "openai",
            "OWL_MODELS__EXTRACTION__NAME": "gpt-x",
            "OWL_MODELS__EXTRACTION__API_KEY": "sk-extraction",
            "OWL_MODELS__EXTRACTION__CONTEXT_WINDOW": "128000",
        },
    )
    extraction = settings.models.model_for(ModelRole.EXTRACTION)
    assert extraction.provider is Provider.OPENAI
    assert extraction.base_url is None  # not inherited key by key from default
    assert extraction.max_tokens == 4096  # the field default, not default's value
    assert settings.models.model_for(ModelRole.JUDGE).name == "local-model"
    assert settings.models.model_for(ModelRole.POLISHING) is settings.models.default


def test_partially_configured_role_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="extraction"):
        load(monkeypatch, LOCAL | {"OWL_MODELS__EXTRACTION__NAME": "other-model"})


def test_extraction_must_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="extraction"):
        load(monkeypatch, {})


def test_unresolvable_later_role_raises_on_use(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(
        monkeypatch,
        {k.replace("__DEFAULT__", "__EXTRACTION__"): v for k, v in LOCAL.items()},
    )
    with pytest.raises(SettingsError, match="judge"):
        settings.models.model_for(ModelRole.JUDGE)


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"PROVIDER": "openai_compatible", "NAME": "m"}, "base_url"),
        ({"PROVIDER": "openai_compatible", "BASE_URL": "http://h/v1"}, "name"),
        ({"PROVIDER": "openai", "API_KEY": "k"}, "name"),
        ({"PROVIDER": "openai", "NAME": "m"}, "api_key"),
        (
            {"PROVIDER": "azure_openai", "API_KEY": "k", "API_VERSION": "v", "DEPLOYMENT": "d"},
            "azure_endpoint",
        ),
        (
            {
                "PROVIDER": "azure_openai",
                "API_KEY": "k",
                "AZURE_ENDPOINT": "https://a",
                "DEPLOYMENT": "d",
            },
            "api_version",
        ),
        (
            {
                "PROVIDER": "azure_openai",
                "API_KEY": "k",
                "AZURE_ENDPOINT": "https://a",
                "API_VERSION": "v",
            },
            "deployment",
        ),
    ],
)
def test_provider_specific_required_keys(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], missing: str
) -> None:
    full = {f"OWL_MODELS__DEFAULT__{k}": v for k, v in env.items()}
    full["OWL_MODELS__DEFAULT__CONTEXT_WINDOW"] = "8000"
    with pytest.raises(ValidationError, match=missing):
        load(monkeypatch, full)


def test_azure_is_accepted_when_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = "OWL_MODELS__DEFAULT__"
    settings = load(
        monkeypatch,
        {
            f"{prefix}PROVIDER": "azure_openai",
            f"{prefix}DEPLOYMENT": "gpt-deploy",
            f"{prefix}AZURE_ENDPOINT": "https://x.openai.azure.com",
            f"{prefix}API_VERSION": "2024-10-21",
            f"{prefix}API_KEY": "azure-secret",
            f"{prefix}CONTEXT_WINDOW": "128000",
            f"{prefix}TPM_LIMIT": "90000",
            f"{prefix}OUTPUT_MODE": "tool",
        },
    )
    model = settings.models.model_for(ModelRole.EXTRACTION)
    assert model.deployment == "gpt-deploy"
    assert model.tpm_limit == 90000
    assert model.output_mode is OutputMode.TOOL


def test_secrets_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, LOCAL)
    model = settings.models.model_for(ModelRole.EXTRACTION)
    assert "lm-studio-secret" not in repr(settings)
    assert "lm-studio-secret" not in str(settings)
    assert "lm-studio-secret" not in settings.model_dump_json()
    assert model.api_key is not None
    assert model.api_key.get_secret_value() == "lm-studio-secret"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("OWL_MODELS__DEFAULT__PROVIDER", "anthropic"),
        ("OWL_MODELS__DEFAULT__CONTEXT_WINDOW", "0"),
        ("OWL_MODELS__DEFAULT__OUTPUT_MODE", "json"),
        ("OWL_CONTEXT__BUDGET_FRACTION", "1.5"),
        ("OWL_CONTEXT__BUDGET_FRACTION", "0"),
        ("OWL_PARSING__DATE_ORDER", "year_first"),
        ("OWL_MATCHER__FUZZY_THRESHOLD", "101"),
    ],
)
def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    with pytest.raises(ValidationError):
        load(monkeypatch, LOCAL | {key: value})


def test_parsing_and_matcher_options(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = load(monkeypatch, LOCAL)
    assert defaults.parsing.to_parse_options() == ParseOptions()
    assert defaults.matcher.to_matcher_config() == MatcherConfig()

    custom = load(
        monkeypatch,
        LOCAL
        | {
            "OWL_PARSING__DATE_ORDER": "month_first",
            "OWL_PARSING__DECIMAL_PLACES": "2",
            "OWL_PARSING__ENUM_SIMILARITY_THRESHOLD": "0.9",
            "OWL_PARSING__ENUM_SIMILARITY_MARGIN": "0.05",
            "OWL_MATCHER__FUZZY_MIN_LENGTH": "30",
            "OWL_MATCHER__FUZZY_THRESHOLD": "95",
        },
    )
    assert custom.parsing.to_parse_options() == ParseOptions(DateOrder.MONTH_FIRST, 2, 0.9, 0.05)
    assert custom.matcher.to_matcher_config() == MatcherConfig(30, 95.0)


def test_context_budget_is_a_fraction_of_the_role_context_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load(monkeypatch, LOCAL).context_budget() == 19660  # floor(32768 * 0.6)
    settings = load(monkeypatch, LOCAL | {"OWL_CONTEXT__BUDGET_FRACTION": "0.5"})
    assert settings.context_budget(ModelRole.EXTRACTION) == 16384


def test_env_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in LOCAL.items()))
    settings = load_settings(env_file=env_file)
    assert settings.models.model_for(ModelRole.EXTRACTION).name == "local-model"


def test_shipped_env_example_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    example = Path(__file__).resolve().parents[3] / ".env.example"
    settings = load_settings(env_file=example)
    assert settings.models.model_for(ModelRole.EXTRACTION).provider is Provider.OPENAI_COMPATIBLE


def test_jobs_max_concurrent_runs_defaults_to_one_and_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load(monkeypatch, LOCAL).jobs.max_concurrent_runs == 1
    env = LOCAL | {"OWL_JOBS__MAX_CONCURRENT_RUNS": "4"}
    assert load(monkeypatch, env).jobs.max_concurrent_runs == 4
