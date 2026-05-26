"""Thin HTTP client for Jarvis's local /chat endpoint.

The digest pipeline routes ALL narrative generation through Jarvis itself
(http://127.0.0.1:8200/chat → openclaw "jarvis" agent, GPT-5.4 + tools)
rather than instantiating its own OpenAI client. This keeps the digest's
voice consistent with the user's voice/chat surface and lets the digest
benefit from any tool access the agent has.

Auth: JARVIS_API_TOKEN env var is sent as `Authorization: Bearer <token>`.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

JARVIS_API_URL = "http://127.0.0.1:8200/chat"
DEFAULT_TIMEOUT_S = 120.0


class JarvisApiError(RuntimeError):
    """Raised when the Jarvis API call fails (network, non-200, missing creds)."""


def chat(
    message: str,
    *,
    session_id: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    url: str = JARVIS_API_URL,
) -> str:
    """POST `message` to Jarvis /chat and return the answer string.

    Raises JarvisApiError on:
      - Missing JARVIS_API_TOKEN env var
      - Network / transport error (timeout, connection refused)
      - Non-200 response
      - Missing `answer` field in the response body
    """
    token = os.environ.get("JARVIS_API_TOKEN")
    if not token:
        raise JarvisApiError(
            "JARVIS_API_TOKEN is not set; cannot call Jarvis /chat. "
            "Source ~/jarvis/config/jarvis.env or set the env var."
        )

    payload: dict[str, str] = {"message": message}
    if session_id is not None:
        payload["session_id"] = session_id

    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise JarvisApiError(f"Jarvis /chat transport error: {exc}") from exc

    if resp.status_code != 200:
        snippet = resp.text[:200].replace("\n", " ")
        raise JarvisApiError(
            f"Jarvis /chat returned {resp.status_code}: {snippet}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise JarvisApiError(f"Jarvis /chat returned non-JSON: {exc}") from exc

    answer = data.get("answer")
    if not isinstance(answer, str):
        raise JarvisApiError(
            f"Jarvis /chat response missing 'answer' string field: {data}"
        )
    return answer
