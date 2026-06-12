---
lat:
  require-code-mention: true
---
# Provider

Builds the pydantic-ai model used by [[summaries#Agent build]] from the `PROVIDER` env var, supporting Ollama (default), LM Studio, OpenRouter, OpenAI, Together, and DeepInfra.

The factory is lazy — invoked per `_build_agent` call rather than at module import — so API-key checks do not fire during test collection. All providers except OpenAI and Together return a fully configured `OpenAIChatModel`; OpenAI and Together use pydantic-ai's `"provider:model"` shorthand which auto-reads the API key from env.

## Provider enum

[[src/youtubebrain/provider.py#Provider]] is a `StrEnum` over `ollama | lmstudio | openrouter | openai | together | deepinfra`.

The string value matches the `PROVIDER` env var. Unknown values raise `ValueError` with message `Unsupported provider: <value>`.

## Model factory

[[src/youtubebrain/provider.py#create_model]] reads `PROVIDER` and `MODEL` after [[src/youtubebrain/config.py#load_env]], then dispatches via `match`.

Per-provider wiring:

- `ollama` / `lmstudio` — `OpenAIChatModel(model_name=MODEL, provider=OpenAIProvider(base_url=f"{HOST}/v1"))`, using `OLLAMA_HOST` / `LMSTUDIO_HOST`. No API-key check.
- `openai` / `together` — call [[provider#API key validation]] for `OPENAI_API_KEY` / `TOGETHER_API_KEY` first, then return the shorthand strings `f"openai:{MODEL}"` / `f"together:{MODEL}"` so pydantic-ai handles model construction internally.
- `deepinfra` — calls [[provider#API key validation]] for `DEEPINFRA_API_KEY`, then `OpenAIChatModel` with `OpenAIProvider(base_url=DEEPINFRA_BASE_URL, api_key=DEEPINFRA_API_KEY)`.
- `openrouter` — calls [[provider#API key validation]] for `OPENROUTER_API_KEY`, then constructs `AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=OPENROUTER_API_KEY)`, wraps it in `OpenRouterProvider(openai_client=...)`, and returns an `OpenAIChatModel`.

## API key validation

[[src/youtubebrain/provider.py#_check_api_key]] raises `ValueError` with `<PROVIDER>_API_KEY is required for the <provider> model provider.` when a cloud provider's key is missing.

Local providers (`ollama`, `lmstudio`) do not call it.

## Tests

Pytest coverage for every dispatch branch plus missing-key and unsupported-value failures; each leaf below maps to one `# @lat:` comment in `tests/test_provider.py`.

### Ollama default

PROVIDER unset defaults to ollama, builds `OpenAIChatModel` with `qwen3:32b` and `base_url=http://localhost:11434/v1`.

### LM Studio host

PROVIDER=lmstudio reads `LMSTUDIO_HOST` and appends `/v1` to form the OpenAI-compatible base URL.

### OpenAI shorthand

PROVIDER=openai validates `OPENAI_API_KEY` via `_check_api_key` and then returns the string `openai:<MODEL>` so pydantic-ai handles model construction internally.

### Together shorthand

PROVIDER=together validates `TOGETHER_API_KEY` via `_check_api_key` and then returns the string `together:<MODEL>` so pydantic-ai handles model construction internally.

### DeepInfra base URL

PROVIDER=deepinfra wires `DEEPINFRA_BASE_URL` and `DEEPINFRA_API_KEY` into `OpenAIProvider`.

### OpenRouter client

PROVIDER=openrouter builds an `AsyncOpenAI` client with `OPENROUTER_BASE_URL` and `OPENROUTER_API_KEY`, then wraps it in `OpenRouterProvider`.

### Missing API key raises

Each cloud provider raises `ValueError` mentioning the expected `<PROVIDER>_API_KEY` env var when it is unset.

### Unsupported provider

An unknown `PROVIDER` value raises `ValueError` with message `Unsupported provider: <value>`.

### API key helper message format

`_check_api_key(None, Provider.openai)` raises `ValueError` whose message contains the uppercased env var name and the lowercased provider name (`OPENAI_API_KEY is required for the openai model provider.`).
