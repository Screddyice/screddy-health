"""Watch on/off/partial classification + transition detection.

Uses the v2 spec's rule:
  - watch_off: ALL of {RHR, HRV, sleep_analysis} are stale
  - watch_on:  ALL of {RHR, HRV, sleep_analysis} are fresh
  - partial:   anything in between (e.g., RHR + HRV fresh during day but no
               overnight sleep_analysis — common when user charges watch
               overnight; sleep-dependent patterns should be gated off)
"""
from __future__ import annotations


WRIST_INDICATOR_METRICS = ("resting_heart_rate", "heart_rate_variability", "sleep_analysis")


def _is_stale(findings: dict, metric: str) -> bool:
    f = findings.get(metric, {})
    return bool(f.get("stale", False))


def classify(findings: dict, *, prior_jsonl: list[dict]) -> dict:
    """Classify today's watch state + detect transitions vs prior JSONL."""
    rhr_stale = _is_stale(findings, "resting_heart_rate")
    hrv_stale = _is_stale(findings, "heart_rate_variability")
    sleep_stale = _is_stale(findings, "sleep_analysis")

    if rhr_stale and hrv_stale and sleep_stale:
        current = "watch_off"
    elif not rhr_stale and not hrv_stale and not sleep_stale:
        current = "watch_on"
    else:
        current = "partial"

    # Yesterday's recorded state (most recent prior entry)
    prior_state = None
    prior_days_since_resume = -1
    if prior_jsonl:
        last = prior_jsonl[-1].get("_data_state", {})
        prior_state = last.get("current")
        prior_days_since_resume = last.get("days_since_resume", -1)

    # Transition
    transition = "none"
    if prior_state == "watch_on" and current == "watch_off":
        transition = "went_off"
    elif prior_state == "watch_off" and current in ("watch_on", "partial"):
        transition = "came_back"

    # gap_days: count consecutive watch_off days
    #   - During a watch_off run: today + prior watch_off entries
    #   - On the came_back day: just count prior watch_off entries (today is not watch_off)
    gap_days = 0
    if current == "watch_off":
        gap_days = 1
    if current == "watch_off" or transition == "came_back":
        for entry in reversed(prior_jsonl):
            if entry.get("_data_state", {}).get("current") == "watch_off":
                gap_days += 1
            else:
                break

    # days_since_resume: 0 on came_back; increment on subsequent watch_on/partial days
    if transition == "came_back":
        days_since_resume = 0
    elif current in ("watch_on", "partial") and prior_days_since_resume >= 0:
        days_since_resume = prior_days_since_resume + 1
    else:
        days_since_resume = -1

    return {
        "current": current,
        "transition": transition,
        "gap_days": gap_days,
        "days_since_resume": days_since_resume,
        "wrist_metrics_fresh": [m for m in WRIST_INDICATOR_METRICS if not _is_stale(findings, m)],
        "wrist_metrics_stale": [m for m in WRIST_INDICATOR_METRICS if _is_stale(findings, m)],
    }


def gap_day_set(prior_jsonl: list[dict]) -> set[str]:
    """Set of YYYY-MM-DD day strings where the recorded state was watch_off.

    Used by metric_fetch.per_metric_finding's exclude_days parameter so the
    baseline math doesn't get poisoned by missing-data days.
    """
    out: set[str] = set()
    for entry in prior_jsonl:
        run_at = entry.get("_run_at", "")
        ds_block = entry.get("_data_state", {})
        if ds_block.get("current") == "watch_off" and len(run_at) >= 10:
            out.add(run_at[:10])
    return out
