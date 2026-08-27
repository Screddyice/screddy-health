"""Thin HTTP client for Screddy's local /chat endpoint.

The digest pipeline routes ALL narrative generation through Screddy itself
(http://127.0.0.1:8200/chat → openclaw "screddy" agent, GPT-5.4 + tools)
rather than instantiating its own OpenAI client. This keeps the digest's
voice consistent with the user's voice/chat surface and lets the digest
benefit from any tool access the agent has.

Auth: SCREDDY_API_TOKEN env var is sent as `Authorization: Bearer <token>`.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

SCREDDY_API_URL = "http://127.0.0.1:8200/chat"
DEFAULT_TIMEOUT_S = 120.0


class ScreddyApiError(RuntimeError):
    """Raised when the Screddy API call fails (network, non-200, missing creds)."""


def chat(
    message: str,
    *,
    session_id: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    url: str = SCREDDY_API_URL,
) -> str:
    """POST `message` to Screddy /chat and return the answer string.

    Raises ScreddyApiError on:
      - Missing SCREDDY_API_TOKEN env var
      - Network / transport error (timeout, connection refused)
      - Non-200 response
      - Missing `answer` field in the response body
    """
    token = os.environ.get("SCREDDY_API_TOKEN")
    if not token:
        raise ScreddyApiError(
            "SCREDDY_API_TOKEN is not set; cannot call Screddy /chat. "
            "Source ~/screddy/config/screddy.env or set the env var."
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
        raise ScreddyApiError(f"Screddy /chat transport error: {exc}") from exc

    if resp.status_code != 200:
        snippet = resp.text[:200].replace("\n", " ")
        raise ScreddyApiError(
            f"Screddy /chat returned {resp.status_code}: {snippet}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise ScreddyApiError(f"Screddy /chat returned non-JSON: {exc}") from exc

    answer = data.get("answer")
    if not isinstance(answer, str):
        raise ScreddyApiError(
            f"Screddy /chat response missing 'answer' string field: {data}"
        )
    return answer
