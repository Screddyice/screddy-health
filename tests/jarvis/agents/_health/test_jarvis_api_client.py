"""Tests for the thin HTTP wrapper around Jarvis's /chat endpoint."""
from __future__ import annotations

import pytest
import respx
from httpx import Response


def test_chat_returns_answer_field(monkeypatch):
    """Successful /chat call returns the `answer` field from the JSON body."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(200, json={"answer": "hello sir", "session_id": "s1"})
        )
        out = jarvis_api_client.chat("Tell me about my week")
    assert out == "hello sir"


def test_chat_sends_authorization_and_message(monkeypatch):
    """The POST body contains the message; Authorization header carries the token."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        route = respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(200, json={"answer": "ok"})
        )
        jarvis_api_client.chat("Tell me about my week")

    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer tok123"
    import json as _json
    body = _json.loads(req.content)
    assert body["message"] == "Tell me about my week"


def test_chat_non_200_raises(monkeypatch):
    """Non-200 response raises with the status code in the message."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(502, text="bad gateway")
        )
        with pytest.raises(jarvis_api_client.JarvisApiError) as excinfo:
            jarvis_api_client.chat("hi")
    assert "502" in str(excinfo.value)


def test_chat_missing_token_raises(monkeypatch):
    """Missing JARVIS_API_TOKEN raises clearly before any network call."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    with pytest.raises(jarvis_api_client.JarvisApiError) as excinfo:
        jarvis_api_client.chat("hi")
    assert "JARVIS_API_TOKEN" in str(excinfo.value)


def test_chat_passes_session_id_when_provided(monkeypatch):
    """Optional session_id appears in the POST body."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        route = respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(200, json={"answer": "ok"})
        )
        jarvis_api_client.chat("hi", session_id="abc-123")

    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body["session_id"] == "abc-123"
