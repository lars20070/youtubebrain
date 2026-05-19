# Flexible Model Provider for Summaries

## Context

`summaries._build_agent` hard-codes `OllamaModel + OllamaProvider` driven by `SUMMARY_MODEL` + `OLLAMA_BASE_URL`. To allow cloud LLMs for summarization, generalise to a `PROVIDER`-dispatched model factory matching the pattern in `tmp/deepresearcher2` ([agents.py:37-102](tmp/deepresearcher2/src/deepresearcher2/agents.py#L37-L102), [config.py:20-26](tmp/deepresearcher2/src/deepresearcher2/config.py#L20-L26)). Ollama remains the default.

## Design

### New module: [src/youtubebrain/provider.py](src/youtubebrain/provider.py)

- `class Provider(StrEnum)`: `ollama | lmstudio | openrouter | openai | together | deepinfra`.
- `Model = str | OpenAIChatModel` type alias.
- `create_model() -> Model`: reads env via `os.environ.get`+`load_dotenv()`, `match` on `Provider`, returns `OpenAIChatModel`/shorthand string. Logs provider+model on call.
- `_check_api_key(key, provider)` helper; raises `ValueError` with `<PROVIDER>_API_KEY is required for the <provider> model provider.`
- Defaults: `PROVIDER=ollama`, `MODEL=qwen3:32b`, `OLLAMA_HOST=http://localhost:11434`, `LMSTUDIO_HOST=http://localhost:1234`, `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`, `DEEPINFRA_BASE_URL=https://api.deepinfra.com/v1/openai`.
- Dispatch (mirror [agents.py:52-102](tmp/deepresearcher2/src/deepresearcher2/agents.py#L52-L102)):
  - ollama/lmstudio → `OpenAIChatModel(MODEL, provider=OpenAIProvider(base_url=f"{host}/v1"))`
  - openai/together → shorthand `f"openai:{model}"` / `f"together:{model}"` (auto env API key)
  - deepinfra → `OpenAIChatModel(MODEL, provider=OpenAIProvider(base_url=DEEPINFRA_BASE_URL, api_key=DEEPINFRA_API_KEY))`
  - openrouter → `AsyncOpenAI(base_url, api_key)` → `OpenRouterProvider(openai_client=...)` → `OpenAIChatModel(MODEL, provider=...)`
  - `case _: raise ValueError(f"Unsupported provider: {provider.value}")`
- Imports: `from pydantic_ai.models.openai import OpenAIChatModel`, `from pydantic_ai.providers.openai import OpenAIProvider`, `from pydantic_ai.providers.openrouter import OpenRouterProvider`, `from openai import AsyncOpenAI`.
- Function-scoped (lazy) — not module-level — so API-key validation does not fire at import (preserves test isolation, unlike [agents.py:105](tmp/deepresearcher2/src/deepresearcher2/agents.py#L105)).

### [src/youtubebrain/summaries.py](src/youtubebrain/summaries.py)

- Drop imports: `OllamaModel`, `OllamaProvider`. Drop constants: `_DEFAULT_MODEL`, `_DEFAULT_BASE_URL`, `_SUMMARY_MODEL_ENV`, `_OLLAMA_BASE_URL_ENV`.
- Add: `from youtubebrain.provider import create_model`. Add `_MODEL_ENV = "MODEL"`, `_DEFAULT_MODEL = "qwen3:32b"`.
- `_build_agent` ([summaries.py:57-64](src/youtubebrain/summaries.py#L57-L64)): `model = create_model(); return Agent(model, output_type=str, system_prompt=SYSTEM_PROMPT, retries=3)`. Drop the `OllamaModel(...)` line.
- `fetch_summaries` ([summaries.py:174](src/youtubebrain/summaries.py#L174)): read `os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)` so the `model` column still records the model id.

### [.env.example](.env.example)

Replace the `SUMMARY_MODEL`/`OLLAMA_BASE_URL` block with:

```
# Summary generation
PROVIDER="ollama"                                        # Either ollama, lmstudio, openrouter, openai, together, or deepinfra
MODEL="qwen3:32b"                                        # Model name as specified by the provider
OLLAMA_HOST="http://localhost:11434"
LMSTUDIO_HOST="http://localhost:1234"
OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
DEEPINFRA_BASE_URL="https://api.deepinfra.com/v1/openai"

# Cloud provider API keys (only the selected PROVIDER's key is required)
# OPENAI_API_KEY="YOUR-API-KEY"
# OPENROUTER_API_KEY="YOUR-API-KEY"
# TOGETHER_API_KEY="YOUR-API-KEY"
# DEEPINFRA_API_KEY="YOUR-API-KEY"
```

### [pyproject.toml](pyproject.toml)

Line 14: `"pydantic-ai-slim[openai]>=1.0"` → `"pydantic-ai-slim[openai,openrouter]>=1.0"`. `openrouter` extra required for `OpenRouterProvider` (confirmed via Context7 docs).

### [tests/test_summaries.py](tests/test_summaries.py)

- Rewrite [test_summaries.py:152-166](tests/test_summaries.py#L152-L166) and [test_summaries.py:169-183](tests/test_summaries.py#L169-L183): monkeypatch `youtubebrain.summaries.create_model` to return a sentinel, and assert `Agent` is called with it. Use `MODEL` not `SUMMARY_MODEL`.
- [test_summaries.py:219](tests/test_summaries.py#L219): unchanged (`qwen3:32b` is still default).

### New: [tests/test_provider.py](tests/test_provider.py)

One test per branch of `create_model`:
- ollama default host+model; lmstudio host override; openai shorthand; together shorthand; deepinfra with `OpenAIProvider(base_url=..., api_key=...)`; openrouter constructs `AsyncOpenAI` then `OpenRouterProvider`.
- `_check_api_key` raises for each cloud provider when key missing.
- Unsupported `PROVIDER` value raises `ValueError`.

Monkeypatch `OpenAIChatModel`, `OpenAIProvider`, `OpenRouterProvider`, `AsyncOpenAI` to record constructor args. No network calls.

### [lat.md/summaries.md](lat.md/summaries.md)

- `Summaries` leading paragraph ([lat.md/summaries.md:5-26](lat.md/summaries.md#L5-L26)): replace "local Ollama model" with "configurable LLM provider (Ollama by default)". Update mermaid `Ollama_via_pydantic_ai` → `Provider_via_pydantic_ai`.
- `Agent build` ([lat.md/summaries.md:57-62](lat.md/summaries.md#L57-L62)): describe `[[src/youtubebrain/provider.py#create_model]]` dispatch on `PROVIDER`, defaulting to ollama.
- `Tests#Default model when env unset` ([lat.md/summaries.md:123-126](lat.md/summaries.md#L123-L126)): "With `MODEL` unset, `create_model` is invoked with default `qwen3:32b`."
- `Tests#SUMMARY_MODEL env override` ([lat.md/summaries.md:127-130](lat.md/summaries.md#L127-L130)): rename → `MODEL env override`. Update `@lat:` comments in the test file accordingly.
- Add new `Provider dispatch` section + `Tests#Provider dispatch` subsection (one leaf per provider + missing-key + unsupported), each backed by a `# @lat:` in `tests/test_provider.py`.

### New: [lat.md/provider.md](lat.md/provider.md)

Sections: leading paragraph; `Provider enum`; `Model factory`; `API key validation`; `Tests` with one leaf per dispatch branch. `require-code-mention: true` in frontmatter.

## Critical files

- New: [src/youtubebrain/provider.py](src/youtubebrain/provider.py), [tests/test_provider.py](tests/test_provider.py), [lat.md/provider.md](lat.md/provider.md)
- Modify: [src/youtubebrain/summaries.py](src/youtubebrain/summaries.py), [tests/test_summaries.py](tests/test_summaries.py), [.env.example](.env.example), [pyproject.toml](pyproject.toml), [lat.md/summaries.md](lat.md/summaries.md)

## Verification

```bash
uv sync
uv run ruff format .
uv run ruff check --fix .
uv run pyright .
uv run pytest -n auto
lat check
```

End-to-end (ollama still works): `PROVIDER=ollama MODEL=qwen3:32b uv run summaries` against a local Ollama. Cloud smoke: `PROVIDER=openai MODEL=gpt-4o-mini OPENAI_API_KEY=… uv run summaries` on one queued video — gated as `paid` if added to CI.

## Unresolved questions

None.
