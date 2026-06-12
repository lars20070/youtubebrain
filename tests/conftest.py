"""Shared pytest fixtures for the youtubebrain test suite."""

from __future__ import annotations

import pytest

from youtubebrain import config


@pytest.fixture(autouse=True)
def _block_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the package reading the on-disk .env during tests; tests set env explicitly.

    config.load_env() is the single dotenv call site, so patching load_dotenv there blocks
    the whole package. Only the .env file is blocked — exported process env vars are left
    untouched, so a test that needs a variable absent must still delenv it.
    """
    monkeypatch.setattr(config, "load_dotenv", lambda *_a, **_k: None)
