"""Telegram one-shot send helper for agents.

Env vars required:
  TELEGRAM_BOT_TOKEN   — bot API token
  TELEGRAM_CHAT_ID     — destination chat id
"""
from __future__ import annotations
import logging
import os
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


def send(text: str, *, chat_id: Optional[str] = None, parse_mode: str = "Markdown") -> bool:
    """Send text to Telegram. Returns True if the API accepted it."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return False
    cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not cid:
        logger.error("TELEGRAM_CHAT_ID not set")
        return False

    # Telegram max 4096 chars per message; chunk if needed
    chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [text]
    for chunk in chunks:
        try:
            resp = httpx.post(
                f"{API_BASE}/bot{token}/sendMessage",
                json={"chat_id": int(cid), "text": chunk, "parse_mode": parse_mode},
                timeout=20.0,
            )
            data = resp.json()
            if not data.get("ok"):
                # Retry without parse_mode in case Markdown parsing failed
                resp = httpx.post(
                    f"{API_BASE}/bot{token}/sendMessage",
                    json={"chat_id": int(cid), "text": chunk},
                    timeout=20.0,
                )
                if not resp.json().get("ok"):
                    logger.error("Telegram send failed: %s", resp.text)
                    return False
        except Exception as exc:
            logger.error("Telegram send errored: %s", exc)
            return False
    return True


if __name__ == "__main__":
    # Quick sanity: python -m jarvis.channels.telegram_notify "hello"
    import sys

    ok = send(sys.argv[1] if len(sys.argv) > 1 else "Jarvis telegram_notify smoke test")
    print("sent" if ok else "failed")
