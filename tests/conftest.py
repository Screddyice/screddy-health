"""Shared test fixtures for screddy tests.

All tests are pure-Python with mocked HAE / Telegram / OpenAI — no network.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def hae_config(tmp_path):
    """Write a synthetic HAE config file and return its path."""
    cfg = {
        "base_url": "https://hae.example.test",
        "read_token": "test-token-xyz",
    }
    p = tmp_path / "apple_health_remote.json"
    p.write_text(json.dumps(cfg))
    return p
