"""HAE client + per-metric aggregation + statistical analysis (z-score, trend, freshness).

The metric registry is the single source of truth for what we pull and how
we treat each metric (aggregation method, freshness threshold, watch dependency).
per_metric_finding consumes the daily-aggregated values and produces the
findings dict that data_state and patterns then operate on.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx

Aggregation = Literal["sum", "avg", "last", "min_overnight"]


@dataclass(frozen=True)
class MetricEntry:
    id: str
    aggregation: Aggregation
    freshness_hours: int
    watch_required: bool


METRIC_REGISTRY: tuple[MetricEntry, ...] = (
    # Existing — preserved from v1
    MetricEntry("step_count", "sum", 24, watch_required=False),
    MetricEntry("active_energy", "sum", 24, watch_required=True),
    MetricEntry("apple_exercise_time", "sum", 24, watch_required=True),
    MetricEntry("resting_heart_rate", "avg", 24, watch_required=True),
    MetricEntry("heart_rate_variability", "avg", 24, watch_required=True),
    # New in v2
    MetricEntry("sleep_analysis", "sum", 36, watch_required=True),
    MetricEntry("blood_oxygen_saturation", "min_overnight", 36, watch_required=True),
    MetricEntry("respiratory_rate", "avg", 36, watch_required=True),
    MetricEntry("walking_heart_rate_average", "avg", 24, watch_required=True),
    MetricEntry("walking_speed", "avg", 24, watch_required=False),
    MetricEntry("walking_asymmetry_percentage", "avg", 24, watch_required=False),
    MetricEntry("vo2_max", "last", 24 * 8, watch_required=True),
    # v3 — recovery + illness signals
    MetricEntry("basal_body_temperature", "avg", 36, watch_required=True),
    MetricEntry("apple_walking_steadiness", "avg", 24 * 7, watch_required=False),
    MetricEntry("mindful_session", "sum", 24, watch_required=False),
    # v3 — activity volume
    MetricEntry("flights_climbed", "sum", 24, watch_required=False),
    MetricEntry("distance_walking_running", "sum", 24, watch_required=False),
    MetricEntry("apple_stand_time", "sum", 24, watch_required=True),
    # v3 — cardiac event signals (Watch-flagged; sparse, zero-or-more events/day,
    # large freshness window so "no events lately" doesn't read as stale)
    MetricEntry("irregular_heart_rhythm_event", "sum", 24 * 30, watch_required=True),
    MetricEntry("high_heart_rate_event", "sum", 24 * 30, watch_required=True),
    MetricEntry("low_heart_rate_event", "sum", 24 * 30, watch_required=True),
)


def fetch_metric(
    *,
    base_url: str,
    token: str,
    metric: str,
    days: int,
    timeout: float = 30.0,
) -> list[dict]:
    """Fetch raw rows for one HAE metric over the last `days` days.

    Uses HAE's `start`/`end` query params (NOT `from`/`to`).
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    url = f"{base_url.rstrip('/')}/api/metrics/{metric}"
    r = httpx.get(
        url,
        headers={"api-key": token},
        params={"start": start, "end": end},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json() or []


def aggregate_daily(rows: list[dict], how: Aggregation) -> dict[str, float]:
    """Bucket raw HAE rows into one value per UTC day.

    `how`:
      - "sum"  — sum all values for the day (cumulative metrics)
      - "avg"  — mean of all values for the day (instant metrics)
      - "last" — most recent value per day (sparse metrics like vo2_max)
      - "min_overnight" — same shape as "last" here; sleep-window filtering
        happens in a higher-level helper since it requires sleep_analysis.
    """
    buckets: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        date_str = row.get("date")
        qty = row.get("qty")
        if not date_str or qty is None:
            continue
        day = date_str[:10]
        buckets.setdefault(day, []).append((date_str, float(qty)))

    out: dict[str, float] = {}
    for day, items in buckets.items():
        values = [v for _, v in items]
        if how == "sum":
            out[day] = sum(values)
        elif how == "avg":
            out[day] = sum(values) / len(values)
        elif how in ("last", "min_overnight"):
            out[day] = max(items, key=lambda iv: iv[0])[1]
        else:
            raise ValueError(f"Unknown aggregation: {how}")
    return out


RECENT_WINDOW_DAYS = 3
TREND_WINDOW_DAYS = 14
MIN_BASELINE_SAMPLES = 7


def per_metric_finding(
    daily: dict[str, float],
    *,
    exclude_days: set[str],
    freshness_hours: int = 24,
) -> dict:
    """Compute z-score, trend, and freshness for one metric's daily values.

    `exclude_days` are days during a watch_off gap; their values do not
    count toward the baseline mean/stdev (gap-aware baseline).
    """
    sorted_days = sorted(daily.keys())
    if len(sorted_days) < MIN_BASELINE_SAMPLES:
        return {
            "status": "insufficient_data",
            "days_with_data": len(sorted_days),
            "need": MIN_BASELINE_SAMPLES,
            "stale": True,
        }

    recent_days = sorted_days[-RECENT_WINDOW_DAYS:]
    baseline_days = [d for d in sorted_days[:-RECENT_WINDOW_DAYS] if d not in exclude_days]
    baseline_values = [daily[d] for d in baseline_days]
    recent_values = [daily[d] for d in recent_days]

    bs_mean = statistics.mean(baseline_values) if baseline_values else 0.0
    bs_stdev = statistics.pstdev(baseline_values) if len(baseline_values) > 1 else 0.0
    recent_mean = statistics.mean(recent_values) if recent_values else 0.0
    z = (recent_mean - bs_mean) / bs_stdev if bs_stdev > 0 else 0.0

    slope, pct = _slope_pct(daily, exclude_days, TREND_WINDOW_DAYS)

    # Freshness: most recent day in `daily` should be within freshness_hours
    most_recent_day = sorted_days[-1]
    most_recent_dt = datetime.fromisoformat(most_recent_day + "T00:00:00+00:00")
    age_hours = (datetime.now(timezone.utc) - most_recent_dt).total_seconds() / 3600
    stale = age_hours > freshness_hours

    return {
        "baseline_mean": bs_mean,
        "baseline_stdev": bs_stdev,
        "recent_mean": recent_mean,
        "recent_days": recent_days,
        "z_score": z,
        "trend_slope_per_day": slope,
        "trend_pct_change_14d": pct,
        "stale": stale,
    }


def _slope_pct(
    daily: dict[str, float],
    exclude_days: set[str],
    window_days: int,
) -> tuple[float, float]:
    """Linear regression slope + percent change over the last window_days
    of data, skipping exclude_days entirely (compresses around them)."""
    sorted_days = [d for d in sorted(daily.keys()) if d not in exclude_days][-window_days:]
    # Need >= 4 points for a meaningful regression slope on noisy daily data;
    # below that, return (0, 0) rather than risk fitting random fluctuations.
    if len(sorted_days) < 4:
        return 0.0, 0.0
    xs = list(range(len(sorted_days)))
    ys = [daily[d] for d in sorted_days]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(len(xs)))
    den = sum((x - mean_x) ** 2 for x in xs) or 1.0
    slope = num / den
    pct = (ys[-1] - ys[0]) / ys[0] if ys[0] else 0.0
    return slope, pct
