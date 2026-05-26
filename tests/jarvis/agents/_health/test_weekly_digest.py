"""Tests for the renamed/generalized weekly digest engine.

Replaces the prior Sunday-only retrospective tests with coverage for:
  - Tue + Sun day gate
  - Distinct Tue vs Sun framing line
  - Glossary block injection
  - Sparse-journal fallback
  - Jarvis client integration (injected callable, mocked)
"""
from __future__ import annotations

from datetime import datetime, timezone


def _make_fake_findings():
    """Minimal findings dict with two glossary-matching metrics."""
    return {
        "resting_heart_rate": {
            "recent_mean": 70.0,
            "z_score": 0.5,
            "trend_pct_change_14d": 0.02,
            "stale": False,
        },
        "heart_rate_variability": {
            "recent_mean": 45.0,
            "z_score": -0.5,
            "trend_pct_change_14d": -0.1,
            "stale": False,
        },
        "_data_state": {"current": "watch_on", "transition": "none", "gap_days": 0},
        "_run_at": "2026-05-21T12:00:00Z",
    }


def test_run_returns_none_on_non_digest_day(monkeypatch):
    """Day gate accepts Tue=1 and Sun=6 only. Wednesday (2) returns None."""
    from jarvis.agents._health import weekly_digest

    out = weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=2,  # Wednesday
        chat_fn=lambda msg, **_: "should not be called",
        load_journal=lambda **_: [],
    )
    assert out is None


def test_run_calls_chat_fn_on_tuesday(monkeypatch):
    """Tuesday (weekday=1) invokes chat_fn and returns its output."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "*Midweek check-in, sir.*\n\nNumbers look stable."

    out = weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    assert out is not None
    assert "Midweek check-in" in out


def test_run_calls_chat_fn_on_sunday(monkeypatch):
    """Sunday (weekday=6) invokes chat_fn and returns its output."""
    from jarvis.agents._health import weekly_digest

    out = weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=6,
        chat_fn=lambda msg, **_: "*Sunday briefing, sir.*\n\nWeek in the books.",
        load_journal=lambda **_: [],
    )
    assert out is not None
    assert "Sunday briefing" in out


def test_tuesday_prompt_uses_midweek_framing():
    """The prompt sent on Tuesday includes the Tuesday headline + framing."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    assert "*Midweek check-in, sir.*" in msg
    assert "Past 7 days through this morning" in msg
    # Should NOT contain the Sunday-specific phrasing
    assert "Sunday briefing" not in msg
    assert "Week in the books" not in msg


def test_sunday_prompt_uses_sunday_framing():
    """The prompt sent on Sunday includes the Sunday headline + framing."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=6,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    assert "*Sunday briefing, sir.*" in msg
    assert "Week in the books" in msg
    assert "Midweek check-in" not in msg


def test_glossary_block_appears_in_prompt():
    """When findings contain metrics with glossary entries, the prompt
    embeds the glossary block."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    assert "### Metric glossary" in msg
    assert "Resting heart rate (RHR)" in msg
    assert "Heart rate variability (HRV)" in msg


def test_sparse_journal_uses_fallback_directive():
    """When fewer than 2 journal entries are available in the 7-day window,
    the prompt directs the model to acknowledge the thin sample."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    # First call: 7-day window returns one entry.
    # Second call (fallback): 30-day window returns the same single entry.
    journal_calls: list[int] = []

    def fake_load_journal(*, days: int, limit: int):
        journal_calls.append(days)
        return [{
            "entry_date": "2026-05-18",
            "text": "[journal] Thursday\n\nEntry date: 2026-05-18\n\nSome stuff.",
            "created_at": datetime(2026, 5, 18, 21, 0, tzinfo=timezone.utc),
            "tags": ["journal", "mental-health"],
        }]

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=fake_load_journal,
    )
    msg = captured["message"]
    # Sparse-journal directive (the "thin sample" wording) is in the prompt
    assert "thin sample" in msg
    # Fallback widened to the 30-day window
    assert 30 in journal_calls


def test_no_journal_entries_uses_empty_directive():
    """When no entries are available at all, the prompt acknowledges
    that no inner-state analysis is possible."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=6,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    assert "no journal entries are available" in msg


def test_run_returns_none_when_chat_returns_empty_string(monkeypatch):
    """An empty chat response yields None (no message dispatched)."""
    from jarvis.agents._health import weekly_digest

    out = weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=1,
        chat_fn=lambda msg, **_: "   ",
        load_journal=lambda **_: [],
    )
    assert out is None


def test_run_returns_none_when_chat_raises(monkeypatch, caplog):
    """If chat_fn raises, run() logs and returns None (caller treats as
    skipped fire, not a crash)."""
    from jarvis.agents._health import jarvis_api_client, weekly_digest

    def raising_chat(*_args, **_kwargs):
        raise jarvis_api_client.JarvisApiError("simulated")

    out = weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_make_fake_findings(),
        today_weekday=1,
        chat_fn=raising_chat,
        load_journal=lambda **_: [],
    )
    assert out is None


# ─── Watch-off fallback: digest still works on phone-only data ────────────


def _watch_off_findings() -> dict:
    """Findings dict representing a watch-off day: phone metrics fresh,
    wrist-derived metrics stale."""
    return {
        # Phone-derived: FRESH (work even when watch is off)
        "step_count": {
            "recent_mean": 8500.0, "z_score": 0.4,
            "trend_pct_change_14d": 0.05, "stale": False,
        },
        "walking_speed": {
            "recent_mean": 1.35, "z_score": 0.1,
            "trend_pct_change_14d": 0.0, "stale": False,
        },
        "flights_climbed": {
            "recent_mean": 6.0, "z_score": 0.0,
            "trend_pct_change_14d": -0.05, "stale": False,
        },
        # Watch-derived: STALE
        "resting_heart_rate": {
            "recent_mean": 0.0, "z_score": 0.0,
            "trend_pct_change_14d": 0.0, "stale": True,
        },
        "heart_rate_variability": {
            "recent_mean": 0.0, "z_score": 0.0,
            "trend_pct_change_14d": 0.0, "stale": True,
        },
        "sleep_analysis": {"stale": True},
        "blood_oxygen_saturation": {"stale": True},
        "_data_state": {
            "current": "watch_off", "transition": "none",
            "gap_days": 15, "days_since_resume": -1,
        },
        "_run_at": "2026-05-26T19:00:00Z",
    }


def test_prompt_lists_fresh_metrics_under_available():
    """The Data availability block lists every fresh metric under Available."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_watch_off_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    assert "### Data availability this week" in msg
    # Find the data line (starts with **Available** after stripping whitespace),
    # not the instruction text that also mentions "Unavailable"/"Available".
    available_line = next(
        line for line in msg.split("\n") if line.lstrip().startswith("**Available**")
    )
    assert "step_count" in available_line
    assert "walking_speed" in available_line
    assert "flights_climbed" in available_line
    # And does NOT name any stale metric
    assert "resting_heart_rate" not in available_line
    assert "heart_rate_variability" not in available_line


def test_prompt_lists_stale_metrics_under_unavailable():
    """The Data availability block lists every stale metric under Unavailable
    so the LLM knows what to skip."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_watch_off_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    unavailable_line = next(
        line for line in msg.split("\n") if line.lstrip().startswith("**Unavailable**")
    )
    assert "resting_heart_rate" in unavailable_line
    assert "heart_rate_variability" in unavailable_line
    assert "sleep_analysis" in unavailable_line
    # Fresh metrics should NOT appear under Unavailable
    assert "step_count" not in unavailable_line
    assert "walking_speed" not in unavailable_line


def test_prompt_includes_watch_off_acknowledgment_instruction():
    """The prompt instructs the LLM to acknowledge watch-off at the start of
    Last week, not skip the digest."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_watch_off_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    # New instruction tells the LLM to USE phone-derived metrics + acknowledge
    # watch-off briefly rather than skipping the digest
    assert "phone-derived metrics" in msg.lower()
    assert "watch off" in msg.lower() or "watch was off" in msg.lower()
    assert "do not skip the digest" in msg.lower() or "do not skip" in msg.lower()


def test_today_signal_only_contains_fresh_metrics():
    """The today_signal JSON block embedded in the prompt should only
    serialize fresh metrics — no zeroed-out stale data."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_watch_off_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    # Locate the today_signal JSON block in the prompt
    signal_marker = "Today's enriched per-metric signal (fresh only):"
    assert signal_marker in msg
    signal_block = msg.split(signal_marker, 1)[1].split("Recent journal entries", 1)[0]
    # Fresh metrics present
    assert "step_count" in signal_block
    assert "walking_speed" in signal_block
    # Stale metrics absent — their keys never appear in today_signal
    assert "resting_heart_rate" not in signal_block
    assert "heart_rate_variability" not in signal_block


def test_glossary_block_only_documents_fresh_metrics_on_watch_off():
    """When the watch is off, the glossary block should document only the
    phone-derived metrics that are actually fresh."""
    from jarvis.agents._health import weekly_digest

    captured: dict = {}

    def fake_chat(message: str, **_):
        captured["message"] = message
        return "ok"

    weekly_digest.run(
        prior_30d_jsonl=[],
        todays_findings=_watch_off_findings(),
        today_weekday=1,
        chat_fn=fake_chat,
        load_journal=lambda **_: [],
    )
    msg = captured["message"]
    glossary_start = msg.find("### Metric glossary")
    assert glossary_start > 0
    # Glossary section ends at the next "###" heading or "Last 7 days" rollup
    glossary_section = msg[glossary_start:msg.find("Last 7 days physical rollup", glossary_start)]
    # Phone-derived definitions present
    assert "Step count" in glossary_section
    assert "Walking speed" in glossary_section
    # Watch-derived definitions absent — nothing for stale metrics
    assert "Resting heart rate" not in glossary_section
    assert "Heart rate variability" not in glossary_section
