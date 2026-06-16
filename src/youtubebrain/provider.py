"""Build a pydantic-ai model based on the PROVIDER env var."""

from __future__ import annotations

import os
from enum import StrEnum
from urllib.parse import urlparse

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.together import TogetherProvider

from youtubebrain import config, logger

_DEFAULT_MODEL = "qwen3:32b"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_DEFAULT_LMSTUDIO_HOST = "http://localhost:1234"
_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TOGETHER_BASE_URL = "https://api.together.xyz/v1"


# @lat: [[provider#Provider enum]]
class Provider(StrEnum):
    """Supported model providers."""

    ollama = "ollama"
    lmstudio = "lmstudio"
    openrouter = "openrouter"
    openai = "openai"
    together = "together"
    deepinfra = "deepinfra"


# @lat: [[provider#API key validation]]
def _check_api_key(api_key: str | None, provider: Provider) -> str:
    """Return a normalized API key; raise if a required key is missing/blank."""
    normalized = api_key.strip() if isinstance(api_key, str) else ""
    if not normalized:
        error_msg = f"{provider.value.upper()}_API_KEY is required for the {provider.value} model provider."
        logger.error(error_msg)
        raise ValueError(error_msg)
    return normalized


def _check_base_url(env_var: str, default: str) -> str:
    """Return a normalized base URL from env/default; raise when malformed."""
    raw = os.environ.get(env_var, default)
    value = raw.strip()
    parsed = urlparse(value)
    if not value or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        error_msg = f"{env_var} must be an absolute http(s) URL, got {raw!r}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    return value


# @lat: [[provider#Model factory]]
def create_model() -> OpenAIChatModel:
    """Build a pydantic-ai model based on PROVIDER and MODEL env vars."""
    config.load_env()
    provider_value = os.environ.get("PROVIDER", Provider.ollama.value)
    try:
        provider = Provider(provider_value)
    except ValueError as exc:
        error_msg = f"Unsupported provider: {provider_value}"
        logger.error(error_msg)
        raise ValueError(error_msg) from exc

    model_name = os.environ.get("MODEL", _DEFAULT_MODEL)
    logger.info(f"Creating model {model_name!r} for provider {provider.value!r}")

    match provider:
        case Provider.ollama:
            host = os.environ.get("OLLAMA_HOST", _DEFAULT_OLLAMA_HOST)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenAIProvider(base_url=f"{host}/v1"),
            )
        case Provider.lmstudio:
            host = os.environ.get("LMSTUDIO_HOST", _DEFAULT_LMSTUDIO_HOST)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenAIProvider(base_url=f"{host}/v1"),
            )
        case Provider.openrouter:
            api_key = _check_api_key(os.environ.get("OPENROUTER_API_KEY"), Provider.openrouter)
            base_url = _check_base_url("OPENROUTER_BASE_URL", _DEFAULT_OPENROUTER_BASE_URL)
            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenRouterProvider(openai_client=client),
            )
        case Provider.openai:
            api_key = _check_api_key(os.environ.get("OPENAI_API_KEY"), Provider.openai)
            base_url = _check_base_url("OPENAI_BASE_URL", _DEFAULT_OPENAI_BASE_URL)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenAIProvider(base_url=base_url, api_key=api_key),
            )
        case Provider.together:
            api_key = _check_api_key(os.environ.get("TOGETHER_API_KEY"), Provider.together)
            base_url = _check_base_url("TOGETHER_BASE_URL", _DEFAULT_TOGETHER_BASE_URL)
            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            return OpenAIChatModel(
                model_name=model_name,
                provider=TogetherProvider(openai_client=client),
            )
        case Provider.deepinfra:
            api_key = _check_api_key(os.environ.get("DEEPINFRA_API_KEY"), Provider.deepinfra)
            base_url = _check_base_url("DEEPINFRA_BASE_URL", _DEFAULT_DEEPINFRA_BASE_URL)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenAIProvider(base_url=base_url, api_key=api_key),
            )
