---
lat:
  require-code-mention: true
---
# Provider

Builds the pydantic-ai model used by [[summaries#Agent build]] from the `PROVIDER` env var, supporting Ollama (default), LM Studio, OpenRouter, OpenAI, Together, and DeepInfra.

The factory is lazy — invoked per `_build_agent` call rather than at module import — so API-key checks do not fire during test collection. Every provider returns a fully configured `OpenAIChatModel`; cloud `*_BASE_URL` values are overridable and validated as absolute `http(s)` URLs.

## Provider enum

[[src/youtubebrain/provider.py#Provider]] is a `StrEnum` over `ollama | lmstudio | openrouter | openai | together | deepinfra`.

The string value matches the `PROVIDER` env var. Unknown values raise `ValueError` with message `Unsupported provider: <value>`.

## Model factory

[[src/youtubebrain/provider.py#create_model]] reads `PROVIDER` and `MODEL` after [[src/youtubebrain/config.py#load_env]], then dispatches via `match`.

Per-provider wiring:

- `ollama` / `lmstudio` — `OpenAIChatModel(model_name=MODEL, provider=OpenAIProvider(base_url=f"{HOST}/v1"))`, using `OLLAMA_HOST` / `LMSTUDIO_HOST`. No API-key check.
- `openai` — calls [[provider#API key validation]] for `OPENAI_API_KEY`, then `OpenAIChatModel` with `OpenAIProvider(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)`, defaulting to `https://api.openai.com/v1`.
- `together` — calls [[provider#API key validation]] for `TOGETHER_API_KEY`, builds `AsyncOpenAI(base_url=TOGETHER_BASE_URL, api_key=TOGETHER_API_KEY)`, then wraps it in `TogetherProvider(openai_client=...)`, defaulting to `https://api.together.xyz/v1`.
- `deepinfra` — calls [[provider#API key validation]] for `DEEPINFRA_API_KEY`, then `OpenAIChatModel` with `OpenAIProvider(base_url=DEEPINFRA_BASE_URL, api_key=DEEPINFRA_API_KEY)`.
- `openrouter` — calls [[provider#API key validation]] for `OPENROUTER_API_KEY`, then constructs `AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)`, wraps it in `OpenRouterProvider(openai_client=...)`, and returns an `OpenAIChatModel`.

## API key validation

[[src/youtubebrain/provider.py#_check_api_key]] strips whitespace and raises `ValueError` with `<PROVIDER>_API_KEY is required for the <provider> model provider.` when a cloud provider's key is missing or blank.

Local providers (`ollama`, `lmstudio`) do not call it.

## Tests

Pytest coverage for every dispatch branch plus missing-key and unsupported-value failures; each leaf below maps to one `# @lat:` comment in `tests/test_provider.py`.

### Ollama default

PROVIDER unset defaults to ollama, builds `OpenAIChatModel` with `qwen3:32b` and `base_url=http://localhost:11434/v1`.

### LM Studio host

PROVIDER=lmstudio reads `LMSTUDIO_HOST` and appends `/v1` to form the OpenAI-compatible base URL.

### OpenAI base URL

PROVIDER=openai validates `OPENAI_API_KEY` via `_check_api_key` and wires `OPENAI_BASE_URL` (default `https://api.openai.com/v1`) and the key into `OpenAIProvider`.

### OpenAI base URL override

When `OPENAI_BASE_URL` is set, provider construction uses the custom value instead of the built-in OpenAI default.

### Together base URL

PROVIDER=together validates `TOGETHER_API_KEY` via `_check_api_key`, builds `AsyncOpenAI` with `TOGETHER_BASE_URL` (default `https://api.together.xyz/v1`), then wraps it in `TogetherProvider`.

### Together base URL override

When `TOGETHER_BASE_URL` is set, the `AsyncOpenAI` client used by `TogetherProvider` is pointed at the custom endpoint.

### DeepInfra base URL

PROVIDER=deepinfra wires `DEEPINFRA_BASE_URL` and `DEEPINFRA_API_KEY` into `OpenAIProvider`.

### OpenRouter client

PROVIDER=openrouter builds an `AsyncOpenAI` client with `OPENROUTER_BASE_URL` and `OPENROUTER_API_KEY`, then wraps it in `OpenRouterProvider`.

### Missing API key raises

Each cloud provider raises `ValueError` mentioning the expected `<PROVIDER>_API_KEY` env var when it is unset or blank.

### Invalid base URL raises

Each cloud provider raises `ValueError` when its `*_BASE_URL` env var is not a non-empty absolute `http(s)` URL.

### Unsupported provider

An unknown `PROVIDER` value raises `ValueError` with message `Unsupported provider: <value>`.

### API key helper message format

`_check_api_key(None, Provider.openai)` raises `ValueError` whose message contains the uppercased env var name and the lowercased provider name (`OPENAI_API_KEY is required for the openai model provider.`).
