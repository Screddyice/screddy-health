"""Tests for data_state module — watch on/off/partial classification."""
from __future__ import annotations


def _findings(rhr_stale=False, hrv_stale=False, sleep_stale=False, **rest):
    """Helper: build a minimal findings dict with stale flags."""
    f = {
        "resting_heart_rate": {"stale": rhr_stale},
        "heart_rate_variability": {"stale": hrv_stale},
        "sleep_analysis": {"stale": sleep_stale},
    }
    f.update(rest)
    return f


def test_classify_watch_on_when_rhr_and_hrv_fresh():
    from screddy.agents._health import data_state as ds

    state = ds.classify(_findings(), prior_jsonl=[])
    assert state["current"] == "watch_on"


def test_classify_watch_off_when_all_three_stale():
    from screddy.agents._health import data_state as ds

    state = ds.classify(
        _findings(rhr_stale=True, hrv_stale=True, sleep_stale=True),
        prior_jsonl=[],
    )
    assert state["current"] == "watch_off"


def test_classify_partial_when_only_sleep_stale():
    from screddy.agents._health import data_state as ds

    state = ds.classify(_findings(sleep_stale=True), prior_jsonl=[])
    assert state["current"] == "partial"


def test_classify_partial_when_rhr_stale_but_hrv_fresh():
    from screddy.agents._health import data_state as ds

    state = ds.classify(_findings(rhr_stale=True), prior_jsonl=[])
    assert state["current"] == "partial"


def _prior_entry(state: str) -> dict:
    return {"_data_state": {"current": state}}


def test_transition_none_when_state_unchanged():
    from screddy.agents._health import data_state as ds

    state = ds.classify(_findings(), prior_jsonl=[_prior_entry("watch_on")])
    assert state["transition"] == "none"


def test_transition_went_off():
    from screddy.agents._health import data_state as ds

    state = ds.classify(
        _findings(rhr_stale=True, hrv_stale=True, sleep_stale=True),
        prior_jsonl=[_prior_entry("watch_on")],
    )
    assert state["transition"] == "went_off"


def test_transition_came_back():
    from screddy.agents._health import data_state as ds

    state = ds.classify(
        _findings(),
        prior_jsonl=[_prior_entry("watch_off")],
    )
    assert state["transition"] == "came_back"


def test_gap_days_counts_consecutive_watch_off():
    from screddy.agents._health import data_state as ds

    # 3 prior days of watch_off, then today is also watch_off
    prior = [_prior_entry("watch_off")] * 3
    state = ds.classify(
        _findings(rhr_stale=True, hrv_stale=True, sleep_stale=True),
        prior_jsonl=prior,
    )
    assert state["gap_days"] == 4  # today + 3 prior


def test_days_since_resume_counts_up_after_came_back():
    from screddy.agents._health import data_state as ds

    # Yesterday was came_back (transition recorded); today watch_on continues
    prior = [{"_data_state": {"current": "watch_on", "transition": "came_back", "days_since_resume": 0}}]
    state = ds.classify(_findings(), prior_jsonl=prior)
    assert state["days_since_resume"] == 1


def test_days_since_resume_resets_on_came_back():
    from screddy.agents._health import data_state as ds

    state = ds.classify(_findings(), prior_jsonl=[_prior_entry("watch_off")])
    assert state["transition"] == "came_back"
    assert state["days_since_resume"] == 0


def test_gap_days_zero_when_watch_on():
    from screddy.agents._health import data_state as ds

    state = ds.classify(_findings(), prior_jsonl=[_prior_entry("watch_on")])
    assert state["gap_days"] == 0


def test_gap_day_set_from_jsonl_history():
    from screddy.agents._health import data_state as ds

    prior = [
        {"_run_at": "2026-05-05T12:00:00Z", "_data_state": {"current": "watch_on"}},
        {"_run_at": "2026-05-06T12:00:00Z", "_data_state": {"current": "watch_off"}},
        {"_run_at": "2026-05-07T12:00:00Z", "_data_state": {"current": "watch_off"}},
        {"_run_at": "2026-05-08T12:00:00Z", "_data_state": {"current": "watch_off"}},
        {"_run_at": "2026-05-09T12:00:00Z", "_data_state": {"current": "watch_on"}},
    ]
    gap = ds.gap_day_set(prior)
    assert gap == {"2026-05-06", "2026-05-07", "2026-05-08"}
