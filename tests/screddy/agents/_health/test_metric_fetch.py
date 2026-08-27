"""Tests for metric_fetch module."""
from __future__ import annotations

import pytest
import respx
from httpx import Response


V2_METRIC_IDS = {
    "step_count",
    "active_energy",
    "apple_exercise_time",
    "resting_heart_rate",
    "heart_rate_variability",
    "sleep_analysis",
    "blood_oxygen_saturation",
    "respiratory_rate",
    "walking_heart_rate_average",
    "walking_speed",
    "walking_asymmetry_percentage",
    "vo2_max",
}

V3_METRIC_IDS = {
    # Recovery + illness signals
    "basal_body_temperature",
    "apple_walking_steadiness",
    "mindful_session",
    # Activity volume
    "flights_climbed",
    "distance_walking_running",
    "apple_stand_time",
    # Cardiac event signals
    "irregular_heart_rhythm_event",
    "high_heart_rate_event",
    "low_heart_rate_event",
}


def test_metric_registry_includes_all_v2_metrics():
    from screddy.agents._health import metric_fetch as m

    registry_ids = {entry.id for entry in m.METRIC_REGISTRY}
    assert V2_METRIC_IDS.issubset(registry_ids)


def test_metric_registry_includes_all_v3_metrics():
    from screddy.agents._health import metric_fetch as m

    registry_ids = {entry.id for entry in m.METRIC_REGISTRY}
    assert V3_METRIC_IDS.issubset(registry_ids)


def test_metric_registry_freshness_thresholds():
    """Vitals 24h, sleep-derived 36h, vo2_max 8 days, cardiac events 30 days."""
    from screddy.agents._health import metric_fetch as m

    by_id = {e.id: e for e in m.METRIC_REGISTRY}
    assert by_id["resting_heart_rate"].freshness_hours == 24
    assert by_id["sleep_analysis"].freshness_hours == 36
    assert by_id["vo2_max"].freshness_hours == 24 * 8
    assert by_id["irregular_heart_rhythm_event"].freshness_hours == 24 * 30


def test_metric_registry_watch_required_flags():
    """step_count + gait + several v3 metrics are iPhone-derived; rest need watch."""
    from screddy.agents._health import metric_fetch as m

    by_id = {e.id: e for e in m.METRIC_REGISTRY}
    iphone_only = {
        "step_count", "walking_speed", "walking_asymmetry_percentage",
        # v3 iPhone-derived
        "apple_walking_steadiness", "mindful_session",
        "flights_climbed", "distance_walking_running",
    }
    for mid, entry in by_id.items():
        if mid in iphone_only:
            assert entry.watch_required is False, f"{mid} should not require watch"
        else:
            assert entry.watch_required is True, f"{mid} should require watch"


@respx.mock
def test_fetch_metric_calls_hae_with_start_end_params():
    from screddy.agents._health import metric_fetch as m

    route = respx.get("https://hae.example.test/api/metrics/step_count").mock(
        return_value=Response(200, json=[{"date": "2026-05-10T08:00:00Z", "qty": 100}])
    )
    rows = m.fetch_metric(
        base_url="https://hae.example.test",
        token="test-token",
        metric="step_count",
        days=14,
    )
    assert route.called
    call = route.calls[0].request
    assert "start" in call.url.params
    assert "end" in call.url.params
    assert "from" not in call.url.params
    assert call.headers["api-key"] == "test-token"
    assert rows == [{"date": "2026-05-10T08:00:00Z", "qty": 100}]


def test_aggregate_daily_sum():
    from screddy.agents._health import metric_fetch as m

    rows = [
        {"date": "2026-05-10T08:00:00Z", "qty": 100},
        {"date": "2026-05-10T12:00:00Z", "qty": 250},
        {"date": "2026-05-11T08:00:00Z", "qty": 50},
    ]
    daily = m.aggregate_daily(rows, "sum")
    assert daily == {"2026-05-10": 350.0, "2026-05-11": 50.0}


def test_aggregate_daily_avg():
    from screddy.agents._health import metric_fetch as m

    rows = [
        {"date": "2026-05-10T08:00:00Z", "qty": 60},
        {"date": "2026-05-10T20:00:00Z", "qty": 80},
    ]
    assert m.aggregate_daily(rows, "avg") == {"2026-05-10": 70.0}


def test_aggregate_daily_last():
    from screddy.agents._health import metric_fetch as m

    rows = [
        {"date": "2026-04-25T04:11:00Z", "qty": 42.0},
        {"date": "2026-05-08T04:11:00Z", "qty": 41.5},
    ]
    daily = m.aggregate_daily(rows, "last")
    # "last" returns one row per day with the latest value
    assert daily == {"2026-04-25": 42.0, "2026-05-08": 41.5}


def test_aggregate_daily_skips_rows_without_qty_or_date():
    from screddy.agents._health import metric_fetch as m

    rows = [
        {"date": "2026-05-10T08:00:00Z", "qty": 100},
        {"date": "2026-05-10T09:00:00Z"},  # no qty
        {"qty": 50},  # no date
        {"date": None, "qty": 20},
    ]
    assert m.aggregate_daily(rows, "sum") == {"2026-05-10": 100.0}


def test_per_metric_finding_computes_zscore_and_trend():
    from screddy.agents._health import metric_fetch as m

    # 30 days of data with a recent spike
    daily = {f"2026-04-{d:02d}": 50.0 for d in range(11, 31)}
    daily["2026-05-01"] = 50.0
    # Last 3 days are recent (spike)
    daily["2026-05-09"] = 80.0
    daily["2026-05-10"] = 85.0
    daily["2026-05-11"] = 90.0

    finding = m.per_metric_finding(daily, exclude_days=set())
    assert finding["recent_mean"] == pytest.approx(85.0, abs=0.1)
    assert finding["baseline_mean"] == pytest.approx(50.0, abs=0.1)
    assert "z_score" in finding
    assert "trend_pct_change_14d" in finding
    assert "stale" in finding


def test_per_metric_finding_marks_stale_when_no_recent_data():
    from screddy.agents._health import metric_fetch as m

    # All data older than 24 hours (use 2026-04-01..2026-04-02)
    daily = {"2026-04-01": 50.0, "2026-04-02": 55.0}
    finding = m.per_metric_finding(daily, exclude_days=set(), freshness_hours=24)
    assert finding["stale"] is True


def test_per_metric_finding_excludes_gap_days_from_baseline():
    from screddy.agents._health import metric_fetch as m

    # Build a baseline of stable 50s then a 5-day "gap" of fake zero values
    # then 3 fresh days. Without exclude, the zeros poison the baseline.
    daily = {f"2026-04-{d:02d}": 50.0 for d in range(11, 26)}  # 15 stable days
    gap_days = {f"2026-04-{d:02d}" for d in range(26, 31)}
    for d in gap_days:
        daily[d] = 0.0  # poisoned values during gap
    daily["2026-05-09"] = 50.0
    daily["2026-05-10"] = 50.0
    daily["2026-05-11"] = 50.0

    # With exclude_days, baseline_mean should be ~50 (not pulled down by zeros)
    finding = m.per_metric_finding(daily, exclude_days=gap_days)
    assert finding["baseline_mean"] == pytest.approx(50.0, abs=1.0)


def test_per_metric_finding_insufficient_data():
    from screddy.agents._health import metric_fetch as m

    daily = {"2026-05-10": 50.0, "2026-05-11": 55.0}  # only 2 days
    finding = m.per_metric_finding(daily, exclude_days=set())
    assert finding["status"] == "insufficient_data"
