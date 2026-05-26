"""Twice-weekly Jarvis health digest engine.

Fires on Tuesday (midweek check-in) and Sunday (week wrap). Reads:
  - The past 30 days of JSONL audit-log entries from health_monitor
  - The past 7 days of mental-health-tagged journal entries from the brain
    (with a 30-day fallback when the 7-day window is too sparse)
  - A metric glossary so the generated narrative defines what each
    cited number means

Generation is routed through Jarvis's own /chat API (openclaw jarvis
agent, GPT-5.4) — the function takes a `chat_fn` callable so tests can
substitute a stub.

Returns a Telegram-Markdown string ready to send, or None when the day
is wrong, the model returns nothing, or the chat call raises.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Optional

from jarvis import brain
from jarvis.agents._health import glossary, jarvis_api_client

logger = logging.getLogger(__name__)

TUESDAY = 1  # date.weekday(): Mon=0
SUNDAY = 6
DIGEST_DAYS = {TUESDAY, SUNDAY}

# Journal-fetch tuning (matches v3.1 defaults so behavior is preserved).
MENTAL_HEALTH_WINDOW_DAYS = 7
MENTAL_HEALTH_FALLBACK_DAYS = 30
MENTAL_HEALTH_MAX_ENTRIES = 10

# Type aliases for the injectable callables.
ChatFn = Callable[..., str]
LoadJournalFn = Callable[..., list[dict]]


def _default_load_journal(*, days: int, limit: int) -> list[dict]:
    return brain.load_recent_mental_health_entries(days=days, limit=limit)


def run(
    *,
    prior_30d_jsonl: list[dict],
    todays_findings: dict,
    today_weekday: int,
    chat_fn: ChatFn = jarvis_api_client.chat,
    load_journal: LoadJournalFn = _default_load_journal,
) -> Optional[str]:
    """Render the twice-weekly digest message.

    Returns a Telegram-ready Markdown string, or None when:
      - today_weekday is not in DIGEST_DAYS
      - chat_fn returns empty / whitespace-only
      - chat_fn raises (logged, swallowed — caller treats as skipped run)

    The function does NOT send the message. The caller (health_digest.main)
    handles delivery.
    """
    if today_weekday not in DIGEST_DAYS:
        return None

    journal_entries = _load_journal_with_fallback(load_journal)
    prompt = _build_prompt(
        prior_30d_jsonl=prior_30d_jsonl,
        todays_findings=todays_findings,
        journal_entries=journal_entries,
        today_weekday=today_weekday,
    )

    try:
        answer = chat_fn(prompt)
    except jarvis_api_client.JarvisApiError as exc:
        logger.error("digest chat call failed: %s", exc)
        return None

    answer = (answer or "").strip()
    return answer or None


def _load_journal_with_fallback(load_journal: LoadJournalFn) -> list[dict]:
    """Pull recent journal entries; widen to 30-day window when 7-day is sparse."""
    entries = load_journal(
        days=MENTAL_HEALTH_WINDOW_DAYS, limit=MENTAL_HEALTH_MAX_ENTRIES,
    )
    if len(entries) >= 2:
        return entries
    return load_journal(
        days=MENTAL_HEALTH_FALLBACK_DAYS, limit=MENTAL_HEALTH_MAX_ENTRIES,
    )


# ─── Prompt assembly ───────────────────────────────────────────────────────


_HEADLINE_TUE = "*Midweek check-in, sir.*"
_HEADLINE_SUN = "*Sunday briefing, sir.*"

_FRAMING_TUE = (
    "Past 7 days through this morning — here's how things are trending "
    "and what to focus on through the rest of the week."
)
_FRAMING_SUN = (
    "Week in the books — here's the read, and what to set up for the "
    "week ahead."
)


def _summarize_week(prior_30d_jsonl: list[dict]) -> dict:
    """Roll the most recent 7 daily JSONL entries into pattern hit counts
    and state coverage so the LLM works from compact context."""
    last7 = prior_30d_jsonl[-7:]
    pattern_counts: dict[str, int] = {}
    suppressed_counts: dict[str, int] = {}
    states: list[str] = []
    for entry in last7:
        for pat in entry.get("_patterns", []) or []:
            pid = pat.get("id", "?")
            if pat.get("suppressed_by"):
                suppressed_counts[pid] = suppressed_counts.get(pid, 0) + 1
            else:
                pattern_counts[pid] = pattern_counts.get(pid, 0) + 1
        state = (entry.get("_data_state") or {}).get("current")
        if state:
            states.append(state)
    return {
        "days_in_window": len(last7),
        "fired_patterns": pattern_counts,
        "suppressed_patterns": suppressed_counts,
        "data_states": states,
    }


def _build_prompt(
    *,
    prior_30d_jsonl: list[dict],
    todays_findings: dict,
    journal_entries: list[dict],
    today_weekday: int,
) -> str:
    headline = _HEADLINE_TUE if today_weekday == TUESDAY else _HEADLINE_SUN
    framing = _FRAMING_TUE if today_weekday == TUESDAY else _FRAMING_SUN

    week_rollup = _summarize_week(prior_30d_jsonl)
    week_json = json.dumps(week_rollup, indent=2)

    signal_keys = {
        "recent_mean", "z_score", "trend_pct_change_14d", "stale",
        "recent_3d_values", "recent_7d_values", "trend_7d_avg_hours",
        "stdev_7d_hours", "acute_chronic_ratio", "trend_pct_change_90d",
        "acute_7d_total_minutes", "chronic_28d_avg_weekly_minutes",
    }

    # Categorize: fresh metrics get fed to the LLM as today_signal; stale
    # metrics are listed only by name so the model knows what's unavailable
    # without trying to interpret zeroed-out numbers. Phone-derived metrics
    # (step_count, walking_speed, walking_asymmetry, flights_climbed,
    # distance_walking_running, apple_walking_steadiness, mindful_session)
    # stay fresh when the watch is off — the digest can still produce a
    # useful read from those alone.
    fresh_metrics: list[str] = []
    stale_metrics: list[str] = []
    today_signal: dict[str, dict] = {}
    for metric, blob in todays_findings.items():
        if metric.startswith("_") or not isinstance(blob, dict):
            continue
        if blob.get("stale", False):
            stale_metrics.append(metric)
            continue
        fresh_metrics.append(metric)
        today_signal[metric] = {k: v for k, v in blob.items() if k in signal_keys}
    today_json = json.dumps(today_signal, indent=2, default=str)

    availability_block = (
        "### Data availability this week\n"
        f"**Available** (cite these freely): "
        f"{', '.join(sorted(fresh_metrics)) if fresh_metrics else '(none — no fresh metrics at all)'}\n\n"
        f"**Unavailable** (watch off / sensor gap — do NOT reference these): "
        f"{', '.join(sorted(stale_metrics)) if stale_metrics else '(all fresh)'}"
    )

    # Glossary only documents fresh metrics — the LLM shouldn't be tempted
    # to cite definitions for data it can't use.
    glossary_block = glossary.format_for_prompt(todays_findings)
    journal_block, journal_directive = _format_journal_block(journal_entries)

    return (
        "You are Jarvis, Shawn's AI butler. Tone: dry, confident, addresses him as 'sir', "
        "no filler, no hedging. You have read his Apple Health data AND his recent "
        "journal entries for the past 7 days, and you are about to deliver his "
        f"{'Tuesday midweek check-in' if today_weekday == TUESDAY else 'Sunday briefing'}. "
        "This message goes out twice a week — Shawn expects it, so do not punt with "
        "`Nothing notable`; find the signal.\n\n"
        "STRUCTURE the message (Telegram Markdown) exactly like this:\n\n"
        f"{headline}\n\n"
        f"_{framing}_\n\n"
        "_Last week_\n"
        "• 2-4 bullets covering what stood out physically — recovery composite, training "
        "load ratio, sleep consistency, anything else with a real number behind it. When "
        "you cite a metric value, briefly state what it means and what a healthy range "
        "looks like, drawing on the glossary below.\n\n"
        "_What to watch_\n"
        "• 1-2 bullets on physical trends forming under the alert threshold (drifts, slow "
        "declines, anything that would matter if it continues another week).\n\n"
        "_Inner state_\n"
        + journal_directive +
        "_Focus this week_\n"
        "• 1-2 short, concrete directives that integrate physical AND inner-state signals. "
        "Not 'try to sleep more' — something like 'hold lights-out within ±30 min of "
        "midnight' or 'name the preoccupation from Thursday's entry before next session'.\n\n"
        "Total length: 16-24 lines. Use Telegram Markdown (*bold*, _italic_, • bullets). "
        "Never wrap in code fences. Reference specific numbers from the physical data and "
        "specific phrases from the journal — not vibes.\n\n"
        "Mental-health rule: be honest. Shawn wants an accurate read, not reassurance. "
        "Surface what you actually see — recurring themes, contradictions, preoccupations, "
        "shifts in tone or focus across the entries, patterns that connect to the physical "
        "data. Reference specific phrases. No softening, no hedging, no advice to seek help, "
        "no 'I am just an AI' disclaimers. Treat him as an adult reading his own mirror.\n\n"
        "Watch-state rule: phone-derived metrics (steps, walking speed/asymmetry, flights, "
        "distance, mindful minutes) work regardless of watch state — always use them. If "
        "wrist-derived metrics are unavailable this week (watch off), briefly acknowledge "
        "that at the START of _Last week_ (e.g., 'Watch was off this week — read is "
        "phone-derived, sir.') and proceed with the available data. Do NOT skip the digest "
        "because the watch is off. Do NOT reference any metric listed under **Unavailable** "
        "below — those are sensor gaps, not signal. If journal entries are sparse (<2 in "
        "the window), say so explicitly in _Inner state_ rather than fabricating analysis.\n\n"
        f"{availability_block}\n\n"
        f"{glossary_block}\n\n"
        f"Last 7 days physical rollup:\n{week_json}\n\n"
        f"Today's enriched per-metric signal (fresh only):\n{today_json}\n\n"
        f"Recent journal entries (mental-health tagged):\n{journal_block}\n"
    )


def _format_journal_block(entries: list[dict]) -> tuple[str, str]:
    """Return (block_for_prompt, directive_for_section).

    Sparse journals get a transparent directive, not invented psychology.
    """
    if not entries:
        return (
            "(no mental-health journal entries available)",
            "• Acknowledge that no journal entries are available; "
            "no inner-state analysis is possible without them, sir.\n\n",
        )

    rows: list[dict] = []
    for e in entries[:MENTAL_HEALTH_MAX_ENTRIES]:
        rows.append({
            "entry_date": e.get("entry_date"),
            "text": (e.get("text") or "")[:1500],  # cap each entry size
            "ingested_at": (
                e.get("created_at").isoformat()
                if e.get("created_at") is not None else None
            ),
        })
    block = json.dumps(rows, indent=2, default=str)

    if len(entries) < 2:
        directive = (
            "• Note that only one journal entry was available; pull the dominant theme "
            "from it, but flag the thin sample.\n\n"
        )
    else:
        directive = (
            "• 2-3 bullets covering: dominant themes across the week's entries, recurring "
            "concerns or preoccupations, and (if physical signals support it) a correlation "
            "note (e.g., 'HRV dipped the same nights you wrote about work stress on Tue/Thu').\n\n"
        )
    return block, directive
