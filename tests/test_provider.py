"""Unit tests for the PROVIDER-dispatched model factory."""

from __future__ import annotations

from typing import cast

import pytest

from youtubebrain.provider import Provider, _check_api_key, create_model


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every env var the factory consults so a test starts from a clean slate.

    The on-disk .env is already neutralised by the autouse _block_dotenv fixture in conftest.py,
    so clearing the process env here leaves the factory looking at genuinely unset variables.
    """
    for name in (
        "PROVIDER",
        "MODEL",
        "OLLAMA_HOST",
        "LMSTUDIO_HOST",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_API_KEY",
        "DEEPINFRA_BASE_URL",
        "DEEPINFRA_API_KEY",
        "OPENAI_API_KEY",
        "TOGETHER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _stub_openai_chat_model(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record OpenAIChatModel/OpenAIProvider/OpenRouterProvider/AsyncOpenAI constructor calls."""
    calls: list[dict[str, object]] = []

    def fake_model(*, model_name: str, provider: object) -> dict[str, object]:
        record: dict[str, object] = {"kind": "OpenAIChatModel", "model_name": model_name, "provider": provider}
        calls.append(record)
        return record

    def fake_openai_provider(**kwargs: object) -> dict[str, object]:
        record: dict[str, object] = {"kind": "OpenAIProvider", **kwargs}
        calls.append(record)
        return record

    def fake_openrouter_provider(**kwargs: object) -> dict[str, object]:
        record: dict[str, object] = {"kind": "OpenRouterProvider", **kwargs}
        calls.append(record)
        return record

    def fake_async_openai(**kwargs: object) -> dict[str, object]:
        record: dict[str, object] = {"kind": "AsyncOpenAI", **kwargs}
        calls.append(record)
        return record

    monkeypatch.setattr("youtubebrain.provider.OpenAIChatModel", fake_model)
    monkeypatch.setattr("youtubebrain.provider.OpenAIProvider", fake_openai_provider)
    monkeypatch.setattr("youtubebrain.provider.OpenRouterProvider", fake_openrouter_provider)
    monkeypatch.setattr("youtubebrain.provider.AsyncOpenAI", fake_async_openai)
    return calls


# @lat: [[provider#Tests#Ollama default]]
def test_create_model_ollama_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER unset defaults to ollama with qwen3:32b at localhost:11434/v1."""
    _clear_provider_env(monkeypatch)
    calls = _stub_openai_chat_model(monkeypatch)
    result = cast("dict[str, object]", create_model())
    assert result["kind"] == "OpenAIChatModel"
    assert result["model_name"] == "qwen3:32b"
    provider_call = next(c for c in calls if c["kind"] == "OpenAIProvider")
    assert provider_call["base_url"] == "http://localhost:11434/v1"


# @lat: [[provider#Tests#LM Studio host]]
def test_create_model_lmstudio(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=lmstudio uses LMSTUDIO_HOST and appends /v1."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "lmstudio")
    monkeypatch.setenv("MODEL", "local-model")
    monkeypatch.setenv("LMSTUDIO_HOST", "http://lmstudio:9999")
    calls = _stub_openai_chat_model(monkeypatch)
    result = cast("dict[str, object]", create_model())
    assert result["model_name"] == "local-model"
    provider_call = next(c for c in calls if c["kind"] == "OpenAIProvider")
    assert provider_call["base_url"] == "http://lmstudio:9999/v1"


# @lat: [[provider#Tests#OpenAI shorthand]]
def test_create_model_openai_shorthand(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=openai returns the pydantic-ai shorthand string."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "openai")
    monkeypatch.setenv("MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    _stub_openai_chat_model(monkeypatch)
    assert create_model() == "openai:gpt-4o-mini"


# @lat: [[provider#Tests#Together shorthand]]
def test_create_model_together_shorthand(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=together returns the pydantic-ai shorthand string."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "together")
    monkeypatch.setenv("MODEL", "Qwen/Qwen2.5-72B-Instruct")
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")
    _stub_openai_chat_model(monkeypatch)
    assert create_model() == "together:Qwen/Qwen2.5-72B-Instruct"


# @lat: [[provider#Tests#DeepInfra base URL]]
def test_create_model_deepinfra(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=deepinfra wires base_url and api_key into OpenAIProvider."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "deepinfra")
    monkeypatch.setenv("MODEL", "meta-llama/Meta-Llama-3.1-70B-Instruct")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    calls = _stub_openai_chat_model(monkeypatch)
    create_model()
    provider_call = next(c for c in calls if c["kind"] == "OpenAIProvider")
    assert provider_call["base_url"] == "https://api.deepinfra.com/v1/openai"
    assert provider_call["api_key"] == "di-test"


# @lat: [[provider#Tests#OpenRouter client]]
def test_create_model_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=openrouter builds AsyncOpenAI then wraps it in OpenRouterProvider."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "openrouter")
    monkeypatch.setenv("MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    calls = _stub_openai_chat_model(monkeypatch)
    create_model()
    async_call = next(c for c in calls if c["kind"] == "AsyncOpenAI")
    assert async_call["base_url"] == "https://openrouter.ai/api/v1"
    assert async_call["api_key"] == "or-test"
    or_call = next(c for c in calls if c["kind"] == "OpenRouterProvider")
    assert or_call["openai_client"] is async_call


# @lat: [[provider#Tests#Missing API key raises]]
@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("together", "TOGETHER_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("deepinfra", "DEEPINFRA_API_KEY"),
    ],
)
def test_create_model_missing_api_key_raises(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    env_var: str,
) -> None:
    """Each cloud provider raises ValueError when its API key is missing."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", provider)
    _stub_openai_chat_model(monkeypatch)
    with pytest.raises(ValueError, match=env_var):
        create_model()


# @lat: [[provider#Tests#Unsupported provider]]
def test_create_model_unsupported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown PROVIDER values raise ValueError."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="Unsupported provider"):
        create_model()


# @lat: [[provider#Tests#API key helper message format]]
def test_check_api_key_helper_message() -> None:
    """_check_api_key formats the error with uppercase provider name."""
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required for the openai model provider."):
        _check_api_key(None, Provider.openai)
