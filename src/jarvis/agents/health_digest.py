"""health_digest — twice-weekly narrative briefing entry point.

Fires Tue 12:00 PT and Sun 16:00 PT via systemd timer health-digest.timer.
Reads the existing JSONL audit log + the past week of mental-health journal
entries, routes generation through Jarvis's /chat API (openclaw jarvis
agent, GPT-5.4), and delivers the result to Telegram.

Daily silent analysis (analyse → JSONL → brain write → emergency
edge-trigger) is owned by jarvis.agents.health_monitor and runs on its
own timer. This module does NOT call analyse(); it consumes whatever the
daily watchdog has already written.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.agents._health import weekly_digest
from jarvis.channels import telegram_notify

logger = logging.getLogger(__name__)

JSONL_LOG_PATH = Path(
    os.environ.get(
        "JARVIS_HEALTH_JSONL_LOG_PATH",
        str(Path.home() / "logs" / "health-monitor.jsonl"),
    )
)
# Override the digest delivery timezone with JARVIS_HEALTH_TZ (any zoneinfo
# name); defaults to America/Los_Angeles to match the original deployment.
LA_TZ = ZoneInfo(os.environ.get("JARVIS_HEALTH_TZ", "America/Los_Angeles"))

DISCLAIMER = (
    "_Pattern check, not a diagnosis. Talk to a clinician for medical concerns._"
)


def _current_weekday_in_la() -> int:
    """Weekday in America/Los_Angeles (Mon=0..Sun=6)."""
    return datetime.now(timezone.utc).astimezone(LA_TZ).weekday()


def _load_prior_jsonl(limit_lines: int = 30) -> list[dict]:
    """Read up to `limit_lines` tail entries from the JSONL log."""
    if not JSONL_LOG_PATH.exists():
        return []
    lines = JSONL_LOG_PATH.read_text().strip().splitlines()[-limit_lines:]
    out: list[dict] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    today_weekday = _current_weekday_in_la()
    if today_weekday not in weekly_digest.DIGEST_DAYS:
        # Defensive — the timer should already enforce this.
        return 0

    prior = _load_prior_jsonl(limit_lines=30)
    todays_findings = prior[-1] if prior else {}

    if not prior:
        # No health data at all — send a short notice so the user knows
        # the watchdog hasn't been writing.
        msg = (
            "_Digest skipped, sir — no health data in the window. "
            "Check the daily watchdog._\n\n" + DISCLAIMER
        )
        ok = telegram_notify.send(msg)
        return 0 if ok else 1

    answer = weekly_digest.run(
        prior_30d_jsonl=prior,
        todays_findings=todays_findings,
        today_weekday=today_weekday,
    )

    if not answer:
        # The engine returned None — either chat failed or returned empty.
        # Surface this to the user rather than going silent.
        fallback = (
            "_Digest skipped, sir — Jarvis didn't respond. "
            "Will retry on next scheduled fire._\n\n" + DISCLAIMER
        )
        ok = telegram_notify.send(fallback)
        return 1  # always exit 1 here; a digest day with no output is a problem

    message = answer + "\n\n" + DISCLAIMER
    ok = telegram_notify.send(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
