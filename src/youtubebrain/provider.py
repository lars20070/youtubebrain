"""Build a pydantic-ai model based on the PROVIDER env var."""

from __future__ import annotations

import os
from enum import StrEnum

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from youtubebrain import config, logger

_DEFAULT_MODEL = "qwen3:32b"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"
_DEFAULT_LMSTUDIO_HOST = "http://localhost:1234"
_DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"

Model = str | OpenAIChatModel


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
def _check_api_key(api_key: str | None, provider: Provider) -> None:
    """Raise if a required API key is missing for the given provider."""
    if not api_key:
        error_msg = f"{provider.value.upper()}_API_KEY is required for the {provider.value} model provider."
        logger.error(error_msg)
        raise ValueError(error_msg)


# @lat: [[provider#Model factory]]
def create_model() -> Model:
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
            api_key = os.environ.get("OPENROUTER_API_KEY")
            _check_api_key(api_key, Provider.openrouter)
            base_url = os.environ.get("OPENROUTER_BASE_URL", _DEFAULT_OPENROUTER_BASE_URL)
            client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenRouterProvider(openai_client=client),
            )
        case Provider.openai:
            _check_api_key(os.environ.get("OPENAI_API_KEY"), Provider.openai)
            return f"openai:{model_name}"
        case Provider.together:
            _check_api_key(os.environ.get("TOGETHER_API_KEY"), Provider.together)
            return f"together:{model_name}"
        case Provider.deepinfra:
            api_key = os.environ.get("DEEPINFRA_API_KEY")
            _check_api_key(api_key, Provider.deepinfra)
            base_url = os.environ.get("DEEPINFRA_BASE_URL", _DEFAULT_DEEPINFRA_BASE_URL)
            return OpenAIChatModel(
                model_name=model_name,
                provider=OpenAIProvider(base_url=base_url, api_key=api_key),
            )
