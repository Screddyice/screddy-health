"""Email one-shot send helper for agents.

Wraps Composio's GMAIL_SEND_EMAIL action. Jarvis-self-contained — reads
credentials from the environment (loaded by systemd from
~/jarvis/config/jarvis.env). Does NOT read from ~/.openclaw/ or any
other runtime's state.

Env vars required:
  COMPOSIO_API_KEY  — Composio ak_... key
  COMPOSIO_USER_ID  — connected_account user_id (user_uwgmr on NEB)
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://backend.composio.dev/api/v3/tools/execute/GMAIL_SEND_EMAIL"


def send(*, to: str, subject: str, body: str) -> bool:
    """Send a plain-text email via Composio Gmail. Returns True on success."""
    api_key = os.environ.get("COMPOSIO_API_KEY")
    user_id = os.environ.get("COMPOSIO_USER_ID")
    if not api_key or not user_id:
        logger.error(
            "COMPOSIO_API_KEY or COMPOSIO_USER_ID not set — add them to "
            "~/jarvis/config/jarvis.env so the systemd service can load them"
        )
        return False
    if not to:
        logger.error("email_notify.send called without `to` recipient")
        return False

    payload = {
        "user_id": user_id,
        "arguments": {
            "recipient_email": to,
            "subject": subject,
            "body": body,
        },
    }
    try:
        resp = httpx.post(
            API_URL,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
        data = resp.json()
        if data.get("successful") or (data.get("data") or {}).get("successful"):
            return True
        logger.error("Composio Gmail send failed: %s", resp.text[:500])
        return False
    except Exception as exc:
        logger.error("email_notify errored: %s", exc)
        return False
