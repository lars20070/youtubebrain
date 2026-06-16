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
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "TOGETHER_BASE_URL",
        "TOGETHER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _stub_openai_chat_model(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record OpenAIChatModel/provider/AsyncOpenAI constructor calls."""
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

    def fake_together_provider(**kwargs: object) -> dict[str, object]:
        record: dict[str, object] = {"kind": "TogetherProvider", **kwargs}
        calls.append(record)
        return record

    def fake_async_openai(**kwargs: object) -> dict[str, object]:
        record: dict[str, object] = {"kind": "AsyncOpenAI", **kwargs}
        calls.append(record)
        return record

    monkeypatch.setattr("youtubebrain.provider.OpenAIChatModel", fake_model)
    monkeypatch.setattr("youtubebrain.provider.OpenAIProvider", fake_openai_provider)
    monkeypatch.setattr("youtubebrain.provider.OpenRouterProvider", fake_openrouter_provider)
    monkeypatch.setattr("youtubebrain.provider.TogetherProvider", fake_together_provider)
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


# @lat: [[provider#Tests#OpenAI base URL]]
def test_create_model_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=openai wires the default base_url and api_key into OpenAIProvider."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "openai")
    monkeypatch.setenv("MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = _stub_openai_chat_model(monkeypatch)
    result = cast("dict[str, object]", create_model())
    assert result["model_name"] == "gpt-4o-mini"
    provider_call = next(c for c in calls if c["kind"] == "OpenAIProvider")
    assert provider_call["base_url"] == "https://api.openai.com/v1"
    assert provider_call["api_key"] == "sk-test"


# @lat: [[provider#Tests#OpenAI base URL override]]
def test_create_model_openai_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=openai honours a custom OPENAI_BASE_URL."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "openai")
    monkeypatch.setenv("MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/v1")
    calls = _stub_openai_chat_model(monkeypatch)
    create_model()
    provider_call = next(c for c in calls if c["kind"] == "OpenAIProvider")
    assert provider_call["base_url"] == "https://proxy.example.com/v1"


# @lat: [[provider#Tests#Together base URL]]
def test_create_model_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=together uses AsyncOpenAI + TogetherProvider with default base_url and api_key."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "together")
    monkeypatch.setenv("MODEL", "Qwen/Qwen2.5-72B-Instruct")
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")
    calls = _stub_openai_chat_model(monkeypatch)
    result = cast("dict[str, object]", create_model())
    assert result["model_name"] == "Qwen/Qwen2.5-72B-Instruct"
    async_call = next(c for c in calls if c["kind"] == "AsyncOpenAI")
    assert async_call["base_url"] == "https://api.together.xyz/v1"
    assert async_call["api_key"] == "tg-test"
    provider_call = next(c for c in calls if c["kind"] == "TogetherProvider")
    assert provider_call["openai_client"] is async_call


# @lat: [[provider#Tests#Together base URL override]]
def test_create_model_together_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """PROVIDER=together honours a custom TOGETHER_BASE_URL."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", "together")
    monkeypatch.setenv("MODEL", "Qwen/Qwen2.5-72B-Instruct")
    monkeypatch.setenv("TOGETHER_API_KEY", "tg-test")
    monkeypatch.setenv("TOGETHER_BASE_URL", "https://together-proxy.example.com/v1")
    calls = _stub_openai_chat_model(monkeypatch)
    create_model()
    async_call = next(c for c in calls if c["kind"] == "AsyncOpenAI")
    assert async_call["base_url"] == "https://together-proxy.example.com/v1"


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
    ("provider", "env_var", "api_key"),
    [
        ("openai", "OPENAI_API_KEY", None),
        ("openai", "OPENAI_API_KEY", "   "),
        ("together", "TOGETHER_API_KEY", None),
        ("together", "TOGETHER_API_KEY", "   "),
        ("openrouter", "OPENROUTER_API_KEY", None),
        ("openrouter", "OPENROUTER_API_KEY", "   "),
        ("deepinfra", "DEEPINFRA_API_KEY", None),
        ("deepinfra", "DEEPINFRA_API_KEY", "   "),
    ],
)
def test_create_model_missing_api_key_raises(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    env_var: str,
    api_key: str | None,
) -> None:
    """Each cloud provider raises ValueError when its API key is missing or blank."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", provider)
    if api_key is not None:
        monkeypatch.setenv(env_var, api_key)
    _stub_openai_chat_model(monkeypatch)
    with pytest.raises(ValueError, match=env_var):
        create_model()


# @lat: [[provider#Tests#Invalid base URL raises]]
@pytest.mark.parametrize(
    ("provider", "base_url_env", "api_key_env"),
    [
        ("openai", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
        ("together", "TOGETHER_BASE_URL", "TOGETHER_API_KEY"),
        ("openrouter", "OPENROUTER_BASE_URL", "OPENROUTER_API_KEY"),
        ("deepinfra", "DEEPINFRA_BASE_URL", "DEEPINFRA_API_KEY"),
    ],
)
def test_create_model_invalid_base_url_raises(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    base_url_env: str,
    api_key_env: str,
) -> None:
    """Cloud providers reject malformed *_BASE_URL values."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("PROVIDER", provider)
    monkeypatch.setenv(api_key_env, "test-key")
    monkeypatch.setenv(base_url_env, "not-a-url")
    _stub_openai_chat_model(monkeypatch)
    with pytest.raises(ValueError, match=base_url_env):
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


def test_check_api_key_strips_whitespace() -> None:
    """_check_api_key trims surrounding spaces before returning the key."""
    assert _check_api_key("  sk-test  ", Provider.openai) == "sk-test"
