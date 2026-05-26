"""health_monitor — daily pattern recognition on Apple Health data.

v2 orchestrator: wires the _health subpackage helpers together.
Fires daily at 8 AM ET via systemd timer health-monitor.timer.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path

from jarvis import brain
from jarvis.agents._health import data_state, metric_fetch, patterns
from jarvis.agents._health.metric_fetch import METRIC_REGISTRY
from jarvis.channels import email_notify, telegram_notify

logger = logging.getLogger(__name__)

JSONL_LOG_PATH = Path(
    os.environ.get(
        "JARVIS_HEALTH_JSONL_LOG_PATH",
        str(Path.home() / "logs" / "health-monitor.jsonl"),
    )
)
LOOKBACK_DAYS = 30
DISCLAIMER = (
    "_Pattern check, not a diagnosis. Talk to a clinician for medical concerns._"
)
# Emergency state lives inside Jarvis's own state directory — never under
# ~/.openclaw, which belongs to a separate runtime. Jarvis owns its own data.
EMERGENCY_STATE_PATH = Path(
    os.environ.get("JARVIS_HEALTH_STATE_DIR", str(Path.home() / "jarvis" / "state"))
) / "emergency.json"
SICKNESS_STATE_PATH = Path(
    os.environ.get("JARVIS_HEALTH_STATE_DIR", str(Path.home() / "jarvis" / "state"))
) / "sickness.json"
# Set JARVIS_HEALTH_EMERGENCY_EMAIL_TO (or fall back to a generic JARVIS_HEALTH_EMAIL_TO)
# to receive emergency-tier alerts via email in addition to Telegram. Sickness uses the
# same address by default; override with JARVIS_HEALTH_SICKNESS_EMAIL_TO if desired.
EMERGENCY_EMAIL_TO = os.environ.get(
    "JARVIS_HEALTH_EMERGENCY_EMAIL_TO",
    os.environ.get("JARVIS_HEALTH_EMAIL_TO", ""),
)
SICKNESS_EMAIL_TO = os.environ.get(
    "JARVIS_HEALTH_SICKNESS_EMAIL_TO",
    EMERGENCY_EMAIL_TO,
)
WELCOME_BACK_TEMPLATE = (
    "*Welcome back, sir.*\n\n"
    "Wrist data resumed after **{gap_days} days**. Recalibrating baselines from today "
    "forward — pattern alerts paused for ~7 days while the new baseline forms "
    "(matches Apple Vitals' 7-night baseline establishment).\n\n"
    "{disclaimer}"
)


def _load_config() -> dict:
    path = Path.home() / ".openjarvis" / "connectors" / "apple_health_remote.json"
    return json.loads(path.read_text())


def _load_prior_jsonl(limit_lines: int = 60) -> list[dict]:
    """Read up to limit_lines tail entries from the JSONL log."""
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


def _log_findings(findings: dict) -> None:
    JSONL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JSONL_LOG_PATH.open("a") as f:
        f.write(json.dumps(findings, default=str) + "\n")


def _enrich_findings_for_patterns(findings: dict, daily_per_metric: dict) -> dict:
    """Add the recent_3d_values, recent_3d_z_scores, trend_7d_avg_hours, etc.
    that the new patterns expect on top of per_metric_finding's output.

    SIDE EFFECT: mutates the nested per-metric dicts in `findings` in place
    (shallow copy at the top level only). This is intentional — the JSONL
    log then records the enriched fields, which we want for retrospective
    analysis and reproducibility. Returns the same dict for convenience.
    """
    enriched = dict(findings)
    # sleep_analysis: recent_3d_values (hours/night) + trend_7d_avg_hours
    # + recent_7d_values + stdev_7d_hours (for circadian_drift)
    if "sleep_analysis" in daily_per_metric and "sleep_analysis" in enriched:
        sleep_daily = daily_per_metric["sleep_analysis"]
        sorted_days = sorted(sleep_daily.keys())
        recent = [sleep_daily[d] for d in sorted_days[-3:]]
        last7 = [sleep_daily[d] for d in sorted_days[-7:]]
        enriched["sleep_analysis"]["recent_3d_values"] = recent
        enriched["sleep_analysis"]["recent_7d_values"] = last7
        enriched["sleep_analysis"]["trend_7d_avg_hours"] = (
            sum(last7) / len(last7) if last7 else 0.0
        )
        enriched["sleep_analysis"]["stdev_7d_hours"] = (
            statistics.pstdev(last7) if len(last7) >= 2 else 0.0
        )

    # apple_exercise_time: acute (7d) vs chronic (28d) load for training_load_imbalance.
    # Chronic is expressed as average WEEKLY minutes so the ratio is unit-comparable.
    if "apple_exercise_time" in daily_per_metric and "apple_exercise_time" in enriched:
        ex_daily = daily_per_metric["apple_exercise_time"]
        sorted_days = sorted(ex_daily.keys())
        acute_days = sorted_days[-7:]
        chronic_days = sorted_days[-28:]
        acute_total = sum(ex_daily[d] for d in acute_days)
        chronic_total = sum(ex_daily[d] for d in chronic_days)
        chronic_weekly_avg = (chronic_total / len(chronic_days)) * 7 if chronic_days else 0.0
        ratio = (acute_total / chronic_weekly_avg) if chronic_weekly_avg > 0 else None
        enriched["apple_exercise_time"]["acute_7d_total_minutes"] = acute_total
        enriched["apple_exercise_time"]["chronic_28d_avg_weekly_minutes"] = chronic_weekly_avg
        enriched["apple_exercise_time"]["acute_chronic_ratio"] = ratio

    # blood_oxygen_saturation, respiratory_rate, walking_*: recent_3d_z_scores
    for metric in ("blood_oxygen_saturation", "respiratory_rate",
                   "walking_asymmetry_percentage", "walking_speed"):
        if metric not in daily_per_metric or metric not in enriched:
            continue
        m_daily = daily_per_metric[metric]
        sorted_days = sorted(m_daily.keys())
        if len(sorted_days) < 4:
            enriched[metric]["recent_3d_z_scores"] = []
            continue
        baseline = [m_daily[d] for d in sorted_days[:-3]]
        recent = [m_daily[d] for d in sorted_days[-3:]]
        if len(baseline) > 1:
            mu = statistics.mean(baseline)
            sd = statistics.pstdev(baseline)
            enriched[metric]["recent_3d_z_scores"] = (
                [(v - mu) / sd for v in recent] if sd > 0 else [0.0] * len(recent)
            )
        else:
            enriched[metric]["recent_3d_z_scores"] = []

    # vo2_max: trend_pct_change_90d
    if "vo2_max" in daily_per_metric and "vo2_max" in enriched:
        vo2_daily = daily_per_metric["vo2_max"]
        sorted_days = sorted(vo2_daily.keys())[-13:]  # ~13 weekly readings = 90 days
        if len(sorted_days) >= 4:
            first = vo2_daily[sorted_days[0]]
            last = vo2_daily[sorted_days[-1]]
            enriched["vo2_max"]["trend_pct_change_90d"] = (
                (last - first) / first if first else 0.0
            )
        else:
            enriched["vo2_max"]["trend_pct_change_90d"] = 0.0

    # Cardiac event metrics: sparse zero-base-rate events. per_metric_finding
    # marks them stale (insufficient continuous baseline), but for emergency
    # detection we treat HAE-returned-anything as fresh. latest_day_count is
    # the actionable field; emergency_cardiac_event reads it directly.
    for metric in ("irregular_heart_rhythm_event", "high_heart_rate_event",
                   "low_heart_rate_event"):
        if metric not in enriched:
            continue
        if enriched[metric].get("error"):
            # Fetch errored; leave as-is so we don't spuriously "unfreshen" it.
            continue
        m_daily = daily_per_metric.get(metric, {})
        if m_daily:
            sorted_days = sorted(m_daily.keys())
            enriched[metric]["latest_day_count"] = m_daily[sorted_days[-1]]
            enriched[metric]["window_total"] = sum(m_daily.values())
        else:
            enriched[metric]["latest_day_count"] = 0
            enriched[metric]["window_total"] = 0
        # Override the freshness gate — for event metrics, "no events" is
        # valid data, not stale data. The detector itself decides whether
        # the count is actionable.
        enriched[metric]["stale"] = False

    return enriched


def analyse() -> tuple[dict, dict]:
    """Returns (findings, daily_per_metric) — daily_per_metric is needed for
    pattern enrichment."""
    cfg = _load_config()
    base_url = cfg["base_url"]
    token = cfg["read_token"]

    prior = _load_prior_jsonl()
    exclude_days = data_state.gap_day_set(prior)

    findings: dict = {}
    daily_per_metric: dict = {}

    for entry in METRIC_REGISTRY:
        try:
            rows = metric_fetch.fetch_metric(
                base_url=base_url, token=token,
                metric=entry.id, days=LOOKBACK_DAYS,
            )
        except Exception as exc:
            findings[entry.id] = {"error": str(exc), "stale": True}
            continue
        daily = metric_fetch.aggregate_daily(rows, entry.aggregation)
        daily_per_metric[entry.id] = daily
        findings[entry.id] = metric_fetch.per_metric_finding(
            daily, exclude_days=exclude_days, freshness_hours=entry.freshness_hours,
        )

    state = data_state.classify(findings, prior_jsonl=prior)
    findings["_data_state"] = state
    findings["_run_at"] = datetime.now(timezone.utc).isoformat()
    return findings, daily_per_metric


def ask_llm_for_message(findings: dict, fired_patterns: list[dict], data_state_block: dict) -> str:
    """Render a Jarvis-toned Telegram message for the fired patterns.

    When any pattern has severity=emergency, switches to the emergency framing:
    leads with 🚨, gives explicit care recommendation, and notes that real-time
    emergencies are the Watch's job (this is retrospective).

    Uses the OpenAI Python SDK (lazy-imported so the module loads without it).
    Install with `pip install openai` or `uv add openai` if you want this
    pattern-alert rendering path. The digest path (jarvis.agents.health_digest)
    uses jarvis_api_client.chat() instead and does NOT need openai installed.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot render LLM message")
    try:
        from openai import OpenAI  # lazy import — see docstring
    except ImportError as exc:
        raise RuntimeError(
            "openai SDK not installed; install with `pip install openai` (or "
            "the `openai` optional extra) to use ask_llm_for_message. The "
            "digest pipeline (health_digest.py) does not require it."
        ) from exc
    client = OpenAI(api_key=api_key)
    state_note = ""
    if data_state_block.get("days_since_resume", -1) >= 0 and data_state_block["days_since_resume"] < 7:
        state_note = (
            f"NOTE: Watch only resumed {data_state_block['days_since_resume']} days ago — "
            "phrase any anomalies as likely settling-in noise rather than acute concern.\n\n"
        )

    has_emergency = any(p.get("severity") == "emergency" for p in fired_patterns)
    has_sickness = any(p.get("severity") == "sickness" for p in fired_patterns)

    if has_emergency:
        # Force-prioritize emergency patterns over moderate/high anomalies so
        # the LLM focuses on what actually needs action.
        emergency_first = sorted(
            fired_patterns,
            key=lambda p: 0 if p.get("severity") == "emergency" else 1,
        )
        framing = (
            "Write a SHORT Telegram message (10-15 lines, Telegram Markdown) that:\n"
            "1) Opens with `🚨 *EMERGENCY-TIER PATTERN, sir.*` on its own line.\n"
            "2) Names the most severe emergency pattern's headline as a *bold* line.\n"
            "3) States the 2-3 specific numbers that triggered it (concrete, no hedging).\n"
            "4) Gives the care recommendation in this exact shape:\n"
            "   `_If symptomatic now: seek emergency care, sir._`\n"
            "   `_If asymptomatic: schedule clinical evaluation within 48 hours._`\n"
            "5) Closes with: `_Real-time emergencies are handled by the Watch itself; "
            "this is a retrospective pattern flag on the past 24 hours of data._`\n\n"
            "Do NOT include `_Reply here if you'd like to dig in, sir._` — emergency "
            "framing should drive action, not conversation.\n"
            "Do NOT name benign causes — emergency-tier means the concerning interpretation "
            "is dominant.\n"
        )
        patterns_payload = emergency_first
    elif has_sickness:
        # Sickness-tier framing: rest/recovery focus, NOT emergency-care wording.
        sickness_first = sorted(
            fired_patterns,
            key=lambda p: 0 if p.get("severity") == "sickness" else 1,
        )
        framing = (
            "Write a SHORT Telegram message (8-12 lines, Telegram Markdown) that:\n"
            "1) Opens with `🤧 *Early illness signal, sir.*` on its own line.\n"
            "2) Names the most relevant sickness pattern's headline as a *bold* line.\n"
            "3) States the 2-3 specific numbers that triggered it (concrete, no hedging).\n"
            "4) Gives the care recommendation in this exact shape:\n"
            "   `_Rest, hydrate, sleep extra. Ease back on training intensity._`\n"
            "   `_Watch for fever, sore throat, congestion, or fatigue over the next 24-48h._`\n"
            "5) Closes with: `_Pattern-based heads-up, not a diagnosis. Real-time emergencies are handled by the Watch itself._`\n\n"
            "Do NOT use the emergency-tier 'seek emergency care' wording — this is "
            "the sickness early-warning tier, recovery-focused.\n"
        )
        patterns_payload = sickness_first
    else:
        framing = (
            "Write a SHORT Telegram message (8-12 lines max, Telegram Markdown) that:\n"
            "1) Opens with the pattern headline as a *bold* line.\n"
            "2) States the 2-3 specific numbers that triggered it (concrete, no hedging).\n"
            "3) Names the most likely benign cause AND the most likely concerning cause.\n"
            "4) Ends with: '_Reply here if you'd like to dig in, sir._'\n\n"
            "If multiple patterns detected, pick the most severe — don't list them all.\n"
        )
        patterns_payload = fired_patterns

    prompt = (
        "You are Jarvis, Shawn's AI butler. Tone: dry, confident, address him as 'sir', "
        "no filler. Health pattern detection just flagged the patterns below from his "
        "Apple Watch data.\n\n"
        f"{state_note}"
        + framing
        + "\nCRITICAL: Output raw Telegram Markdown ONLY. Do NOT wrap in fences. "
        "Use *bold*, _italic_, • bullets. Avoid characters Telegram requires escaping "
        "outside formatting markers.\n\n"
        f"Patterns:\n{json.dumps(patterns_payload, indent=2, default=str)}\n\n"
        f"Underlying numbers:\n{json.dumps({k: v for k, v in findings.items() if not k.startswith('_')}, indent=2, default=str)}\n"
    )
    if has_emergency:
        max_tokens = 500
    elif has_sickness:
        max_tokens = 450
    else:
        max_tokens = 400
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return (r.choices[0].message.content or "").strip()


def _load_tier_state(state_path: Path) -> dict:
    """Read a tier-state file (emergency or sickness). Returns {} on missing/unreadable."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read state file %s: %s", state_path, exc)
        return {}


def _save_tier_state(state_path: Path, state: dict) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, default=str))
    except OSError as exc:
        logger.warning("could not write state file %s: %s", state_path, exc)


def _filter_tier_for_alert(
    fired_patterns: list[dict],
    *,
    severity: str,
    state_path: Path,
) -> list[dict]:
    """Edge-trigger filter: return the subset of fired patterns at the given
    severity tier that should alert NOW (i.e., not previously active per the
    state file). Persists current active set so future runs can compare.

    Used for severity-tier dedup of emergency and sickness patterns — both
    follow the same edge-trigger model with their own state file.
    """
    state = _load_tier_state(state_path)
    last_active = set(state.get("active_pattern_ids", []) or [])
    tier_patterns = [p for p in fired_patterns if p.get("severity") == severity]
    current_ids = {p["id"] for p in tier_patterns}
    to_alert = [p for p in tier_patterns if p["id"] not in last_active]
    _save_tier_state(state_path, {
        "active_pattern_ids": sorted(current_ids),
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "last_alerted_ids": sorted(p["id"] for p in to_alert) if to_alert else [],
    })
    return to_alert


def _send_emergency_email(message_body: str, fired_patterns: list[dict]) -> bool:
    """Mirror an emergency Telegram message to email as an archival/redundant
    channel. Falls back to no-op when no email recipient is configured
    (JARVIS_HEALTH_EMERGENCY_EMAIL_TO / JARVIS_HEALTH_EMAIL_TO env vars)."""
    if not fired_patterns or not EMERGENCY_EMAIL_TO:
        return False
    headlines = [p.get("headline", p.get("id", "?")) for p in fired_patterns]
    subject = "[Jarvis] Health emergency-tier pattern: " + "; ".join(headlines[:2])
    body = (
        "Jarvis flagged one or more emergency-tier patterns from your Apple Health data.\n\n"
        "This is a retrospective pattern review, not a real-time alert (the Apple Watch "
        "handles real-time emergencies natively).\n\n"
        "Telegram message (also delivered):\n"
        "----------------------------------------\n"
        f"{message_body}\n"
        "----------------------------------------\n\n"
        "Raw pattern details:\n"
        f"{json.dumps(fired_patterns, indent=2, default=str)}\n"
    )
    return email_notify.send(to=EMERGENCY_EMAIL_TO, subject=subject, body=body)


def _send_sickness_email(message_body: str, fired_patterns: list[dict]) -> bool:
    """Mirror a sickness-tier Telegram message to email as an archival/redundant
    channel. Tone is rest/recovery focused, NOT emergency framing. Falls back
    to no-op when no email recipient is configured (JARVIS_HEALTH_SICKNESS_EMAIL_TO
    / JARVIS_HEALTH_EMERGENCY_EMAIL_TO / JARVIS_HEALTH_EMAIL_TO env vars)."""
    if not fired_patterns or not SICKNESS_EMAIL_TO:
        return False
    headlines = [p.get("headline", p.get("id", "?")) for p in fired_patterns]
    subject = "[Jarvis] Early illness signal detected: " + "; ".join(headlines[:2])
    body = (
        "Jarvis flagged an early illness signal from your Apple Health data.\n\n"
        "Multiple vital markers are drifting in the direction that typically\n"
        "precedes illness onset by 1-3 days. This is a pattern-based heads-up,\n"
        "not a diagnosis or an emergency — real-time emergencies are handled\n"
        "by the Watch itself.\n\n"
        "Suggested response: rest, hydrate, sleep extra, ease training intensity,\n"
        "and watch for fever, sore throat, congestion, or fatigue over the next\n"
        "24-48 hours.\n\n"
        "Telegram message (also delivered):\n"
        "----------------------------------------\n"
        f"{message_body}\n"
        "----------------------------------------\n\n"
        "Raw pattern details:\n"
        f"{json.dumps(fired_patterns, indent=2, default=str)}\n"
    )
    return email_notify.send(to=SICKNESS_EMAIL_TO, subject=subject, body=body)


def main() -> int:
    try:
        findings, daily_per_metric = analyse()
    except Exception as exc:
        try:
            _log_findings({
                "_run_at": datetime.now(timezone.utc).isoformat(),
                "_error": str(exc),
            })
        except Exception:
            pass
        logger.exception("analyse() failed")
        return 2

    state = findings.get("_data_state", {})
    findings["_patterns"] = []  # populated below if not on came_back rebuild

    # came_back transition: send welcome-back, skip pattern detection this run
    if state.get("transition") == "came_back":
        _log_findings(findings)
        msg = WELCOME_BACK_TEMPLATE.format(
            gap_days=state.get("gap_days", 0),
            disclaimer=DISCLAIMER,
        )
        ok = telegram_notify.send(msg)
        return 0 if ok else 1

    # Within 7-day post-resume rebuild window: also skip patterns
    days_since_resume = state.get("days_since_resume", -1)
    if 0 <= days_since_resume < 7:
        _log_findings(findings)
        return 0

    # Normal path: enrich findings, detect patterns, anti-spam, log, deliver
    enriched = _enrich_findings_for_patterns(findings, daily_per_metric)
    fired = patterns.detect_all(enriched, gated_by=state.get("current", "watch_on"))
    prior_3d = _load_prior_jsonl(limit_lines=3)
    fired = patterns.anti_spam_filter(fired, prior_jsonl_3d=prior_3d)
    findings["_patterns"] = fired
    _log_findings(findings)

    # Sync today's per-metric values into the Jarvis brain (Postgres).
    try:
        run_at_dt = datetime.fromisoformat(findings["_run_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        run_at_dt = datetime.now(timezone.utc)
    inserted = brain.write_health_metrics(daily_per_metric, enriched, run_at=run_at_dt)
    if inserted:
        logger.info("wrote %d health_metrics rows to brain", inserted)

    # Deliver
    unsuppressed = [p for p in fired if not p.get("suppressed_by")]

    # Emergency- and sickness-tier edge-triggering: only alert on transitions,
    # not on every run while the pattern persists. Each tier has its own state
    # file so they dedupe independently.
    emergency_to_alert = _filter_tier_for_alert(
        unsuppressed, severity="emergency", state_path=EMERGENCY_STATE_PATH,
    )
    sickness_to_alert = _filter_tier_for_alert(
        unsuppressed, severity="sickness", state_path=SICKNESS_STATE_PATH,
    )
    non_emergency = [
        p for p in unsuppressed if p.get("severity") not in ("emergency", "sickness")
    ]

    # If nothing fresh to alert on (already-active emergencies/sickness suppressed
    # AND no other patterns), stay silent.
    if not non_emergency and not emergency_to_alert and not sickness_to_alert:
        return 0

    parts: list[str] = []
    # Emergency leads, then sickness, then non-emergency.
    llm_input: list[dict] = []
    if emergency_to_alert:
        llm_input.extend(emergency_to_alert)
    if sickness_to_alert:
        llm_input.extend(sickness_to_alert)
    llm_input.extend(non_emergency)
    if llm_input:
        try:
            parts.append(ask_llm_for_message(enriched, llm_input, state))
        except Exception as exc:
            parts.append(f"Health pattern detected, but LLM rendering failed: {exc}")
    parts.append(DISCLAIMER)

    message = "\n\n".join(parts)
    delivered = telegram_notify.send(message)

    # Mirror to email when an emergency alert is firing fresh.
    if emergency_to_alert:
        try:
            _send_emergency_email(message, emergency_to_alert)
        except Exception:
            logger.exception("emergency email mirror failed")

    # Mirror to email when a sickness alert is firing fresh.
    if sickness_to_alert:
        try:
            _send_sickness_email(message, sickness_to_alert)
        except Exception:
            logger.exception("sickness email mirror failed")

    return 0 if delivered else 1


if __name__ == "__main__":
    raise SystemExit(main())
