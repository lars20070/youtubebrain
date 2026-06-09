"""Shared pytest fixtures for the youtubebrain test suite."""

from __future__ import annotations

import pytest

from youtubebrain import clusters, descriptions, embeddings, provider, transcripts

# Every youtubebrain module that calls load_dotenv() at runtime. Patched as a block so no test
# accidentally reads the developer's on-disk .env when it deletes or relies on a pipeline env var.
_DOTENV_MODULES = (clusters, descriptions, embeddings, provider, transcripts)


@pytest.fixture(autouse=True)
def _block_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop every youtubebrain module reading the on-disk .env during tests; tests set env explicitly.

    Only the .env file is blocked — exported process env vars are left untouched, so a test that
    needs a variable absent must still delenv it.
    """
    for module in _DOTENV_MODULES:
        monkeypatch.setattr(module, "load_dotenv", lambda *_a, **_k: None)
