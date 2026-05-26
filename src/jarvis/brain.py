"""Brain — optional Postgres-backed durable store for observations and
health metrics.

Two integration points used by the health digest:

  - `write_health_metrics(daily_per_metric, findings, *, run_at, source)`
    persists per-metric daily readings to a `health_metrics` table so
    historical trends survive across systemd restarts and JSONL rotation.

  - `load_recent_mental_health_entries(days, limit)` reads
    `mental-health` + `journal` tagged observations to feed the weekly
    digest's "Inner state" section.

Both helpers gracefully no-op when `DATABASE_URL` is unset, so the
digest pipeline works without any database at all — JSONL becomes the
sole durable record and `Inner state` falls back to "no journal entries
available" framing. Plug in your own Postgres (Neon, RDS, local) by
exporting `DATABASE_URL`; expected schema is documented in the spec at
`docs/health-digest-design.md`.
"""
from __future__ import annotations

import contextlib
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

import psycopg
from psycopg.types.json import Json

logger = logging.getLogger(__name__)


def database_url() -> Optional[str]:
    """DATABASE_URL from env. Sourced via systemd from jarvis.env."""
    return os.environ.get("DATABASE_URL")


@contextlib.contextmanager
def connect() -> Iterator[Optional[psycopg.Connection]]:
    """Open a Postgres connection. Yields None when DATABASE_URL is unset
    so callers can `if conn is None: return` without raising."""
    url = database_url()
    if not url:
        logger.debug("DATABASE_URL not set; brain.connect() yielding None")
        yield None
        return
    with psycopg.connect(url) as conn:
        yield conn


# ─── health_metrics writes ────────────────────────────────────────────────

_UNIT_MAP = {
    "step_count": "count",
    "flights_climbed": "count",
    "active_energy": "kcal",
    "apple_exercise_time": "min",
    "apple_stand_time": "min",
    "mindful_session": "min",
    "resting_heart_rate": "bpm",
    "walking_heart_rate_average": "bpm",
    "heart_rate_variability": "ms",
    "sleep_analysis": "hours",
    "blood_oxygen_saturation": "percent",
    "walking_asymmetry_percentage": "percent",
    "apple_walking_steadiness": "percent",
    "respiratory_rate": "breaths_per_min",
    "walking_speed": "m_per_s",
    "vo2_max": "ml_kg_min",
    "basal_body_temperature": "celsius",
    "distance_walking_running": "km",
    "irregular_heart_rhythm_event": "events",
    "high_heart_rate_event": "events",
    "low_heart_rate_event": "events",
}


def write_health_metrics(
    daily_per_metric: dict[str, dict[str, float]],
    findings: dict,
    *,
    run_at: datetime,
    source: str = "apple_health",
) -> int:
    """Insert one row per metric for today's run into health_metrics.

    `value` is the most recent day's aggregated value. `raw` jsonb stores
    the full per-metric finding for replay. `date` = run_at (consistent
    with existing rows from Track C's backfill).

    Returns rows inserted. 0 on graceful degradation (no DATABASE_URL,
    or any DB error — health monitor must never crash on brain hiccups).
    """
    if not daily_per_metric:
        return 0
    rows: list[tuple] = []
    for metric_id, daily in daily_per_metric.items():
        if not daily:
            continue
        sorted_days = sorted(daily.keys())
        latest_value = daily[sorted_days[-1]]
        if isinstance(latest_value, float) and (math.isnan(latest_value) or math.isinf(latest_value)):
            continue
        finding = findings.get(metric_id) or {}
        rows.append((
            metric_id,
            run_at,
            float(latest_value),
            _UNIT_MAP.get(metric_id, "unknown"),
            source,
            Json({
                "latest_day": sorted_days[-1],
                "days_in_window": len(sorted_days),
                "finding": _scrub_for_jsonb(finding),
            }),
        ))
    if not rows:
        return 0
    try:
        with connect() as conn:
            if conn is None:
                return 0
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO health_metrics (id, metric, date, value, unit, source, raw)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
                conn.commit()
        return len(rows)
    except Exception as exc:
        logger.warning("health_metrics write failed: %s", exc)
        return 0


def _scrub_for_jsonb(d):
    """JSONB doesn't accept NaN/inf. Recursively convert to None."""
    if isinstance(d, dict):
        return {k: _scrub_for_jsonb(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_scrub_for_jsonb(x) for x in d]
    if isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return None
    return d


# ─── mental-health journal reads ──────────────────────────────────────────

_JOURNAL_DATE_PATTERN = re.compile(r"Entry date:\s*(\d{4}-\d{2}-\d{2})")


def load_recent_mental_health_entries(
    *, days: int = 7, limit: int = 14,
) -> list[dict]:
    """Read recent mental-health-tagged journal entries from the brain.

    Filters by the JOURNAL entry date parsed from body text — NOT by
    created_at, which reflects ingestion time and can be much later than
    when the entry was actually written. Returns most-recent-first.

    Each entry dict: {entry_date: 'YYYY-MM-DD' | None, text: str,
    created_at: datetime, tags: list[str]}.
    """
    try:
        with connect() as conn:
            if conn is None:
                return []
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT text, tags, created_at
                    FROM observations
                    WHERE tags && ARRAY['mental-health','journal']::text[]
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (max(limit * 4, 60),),
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning("mental-health entry read failed: %s", exc)
        return []

    today = datetime.now(timezone.utc).date()
    out: list[dict] = []
    for text, tags, created_at in rows:
        match = _JOURNAL_DATE_PATTERN.search(text or "")
        entry_date_str = match.group(1) if match else None
        try:
            entry_date = (
                datetime.strptime(entry_date_str, "%Y-%m-%d").date()
                if entry_date_str else None
            )
        except ValueError:
            entry_date = None
        if entry_date is not None and (today - entry_date).days > days:
            continue
        out.append({
            "entry_date": entry_date.isoformat() if entry_date else None,
            "text": text or "",
            "created_at": created_at,
            "tags": list(tags or []),
        })
        if len(out) >= limit:
            break
    out.sort(
        key=lambda r: (r["entry_date"] is None, r["entry_date"] or ""),
        reverse=True,
    )
    return out
