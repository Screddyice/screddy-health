"""Integration tests for health_monitor orchestrator."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


_EVENT_METRICS = (
    "irregular_heart_rhythm_event",
    "high_heart_rate_event",
    "low_heart_rate_event",
)


def _hae_response(metric: str, days_of_data: int = 14):
    """Build a synthetic HAE response with `days_of_data` consecutive days
    ending today, each row qty=<metric-appropriate default>.

    Event-type metrics (cardiac alerts) return empty by default — the
    realistic case is zero events, and qty=50 would fake 50 AFib events
    per day and trip the emergency detector during unrelated tests.

    Most metrics use a neutral 50.0. blood_oxygen_saturation uses 98.0
    so it doesn't trip sickness_signal's absolute-floor threshold (<94%).
    """
    if metric in _EVENT_METRICS:
        return []
    from datetime import datetime, timedelta, timezone
    # Metric-appropriate constants: a flat 50.0 is fine for most metrics
    # (yields stdev=0 → z=0) but breaks for SpO2 because the sickness_signal
    # detector treats recent_mean<94 as a hard absolute floor.
    qty_by_metric = {"blood_oxygen_saturation": 98.0}
    qty = qty_by_metric.get(metric, 50.0)
    rows = []
    now = datetime.now(timezone.utc)
    for i in range(days_of_data):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%dT12:00:00Z")
        rows.append({"date": d, "qty": qty})
    return rows


def test_main_came_back_sends_welcome_message_and_skips_patterns(tmp_path, monkeypatch):
    """When transition is came_back, send welcome-back, skip pattern detection."""
    import respx
    from httpx import Response
    from jarvis.agents import health_monitor as hm

    # Fake JSONL log path
    log_path = tmp_path / "health-monitor.jsonl"
    monkeypatch.setattr(hm, "JSONL_LOG_PATH", log_path)

    # Prior JSONL: yesterday was watch_off
    log_path.write_text(json.dumps({
        "_run_at": "2026-05-10T12:00:00Z",
        "_data_state": {"current": "watch_off"},
    }) + "\n")

    # HAE config
    cfg_path = tmp_path / "apple_health_remote.json"
    cfg_path.write_text(json.dumps({"base_url": "https://hae.example.test", "read_token": "tok"}))
    monkeypatch.setattr(hm, "_load_config", lambda: json.loads(cfg_path.read_text()))

    # Mock all HAE responses (every metric returns 14 days of data → fresh)
    sent_messages = []
    monkeypatch.setattr(hm.telegram_notify, "send", lambda text: sent_messages.append(text) or True)

    with respx.mock:
        for entry_id in (e.id for e in hm.METRIC_REGISTRY):
            respx.get(f"https://hae.example.test/api/metrics/{entry_id}").mock(
                return_value=Response(200, json=_hae_response(entry_id))
            )
        rc = hm.main()

    assert rc == 0
    assert len(sent_messages) == 1
    assert "welcome back" in sent_messages[0].lower()


def test_main_anti_spam_suppresses_repeat_pattern(tmp_path, monkeypatch):
    """When same pattern fired (unsuppressed) yesterday, today's repeat is suppressed."""
    import respx
    from httpx import Response
    from jarvis.agents import health_monitor as hm

    log_path = tmp_path / "health-monitor.jsonl"
    monkeypatch.setattr(hm, "JSONL_LOG_PATH", log_path)
    # Yesterday: hrv_trend_down fired unsuppressed
    log_path.write_text(json.dumps({
        "_run_at": "2026-05-10T12:00:00Z",
        "_data_state": {"current": "watch_on"},
        "_patterns": [{"id": "hrv_trend_down", "severity": "low"}],
    }) + "\n")

    cfg_path = tmp_path / "apple_health_remote.json"
    cfg_path.write_text(json.dumps({"base_url": "https://hae.example.test", "read_token": "tok"}))
    monkeypatch.setattr(hm, "_load_config", lambda: json.loads(cfg_path.read_text()))

    sent_messages = []
    monkeypatch.setattr(hm.telegram_notify, "send", lambda text: sent_messages.append(text) or True)
    monkeypatch.setattr(hm, "ask_llm_for_message",
                        lambda findings, patterns, data_state: "ALERT")

    # Build HAE responses where HRV is trending strongly down (-25%) so pattern would fire
    def hrv_decline_rows():
        from datetime import datetime, timedelta, timezone
        rows = []
        now = datetime.now(timezone.utc)
        for i in range(14):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%dT12:00:00Z")
            # Older data = higher HRV; recent = lower
            qty = 50.0 - (13 - i) * 1.0
            rows.append({"date": d, "qty": qty})
        return rows

    with respx.mock:
        for entry in hm.METRIC_REGISTRY:
            if entry.id == "heart_rate_variability":
                respx.get(f"https://hae.example.test/api/metrics/{entry.id}").mock(
                    return_value=Response(200, json=hrv_decline_rows())
                )
            else:
                respx.get(f"https://hae.example.test/api/metrics/{entry.id}").mock(
                    return_value=Response(200, json=_hae_response(entry.id))
                )
        rc = hm.main()

    assert rc == 0
    # Telegram NOT called because hrv_trend_down was suppressed (low severity, repeat within 3d)
    assert sent_messages == []
    # JSONL should still record the suppressed pattern
    last = json.loads(log_path.read_text().strip().splitlines()[-1])
    fired = last.get("_patterns", [])
    suppressed = [p for p in fired if p.get("id") == "hrv_trend_down"]
    assert suppressed and suppressed[0].get("suppressed_by") == "3d_cooldown"


def test_main_sickness_pattern_alerts_via_telegram_and_email(tmp_path, monkeypatch):
    """When a NEW sickness-tier pattern fires (not previously active),
    main() sends a sickness-framed Telegram + email mirror.

    Uses a direct mock of patterns.detect_all to keep the test deterministic
    — exercising the sickness-routing plumbing in main() rather than the
    detector logic (which is covered exhaustively by unit tests).
    """
    import respx
    from httpx import Response
    from jarvis.agents import health_monitor as hm

    log_path = tmp_path / "health-monitor.jsonl"
    monkeypatch.setattr(hm, "JSONL_LOG_PATH", log_path)
    sickness_state = tmp_path / "sickness.json"
    monkeypatch.setattr(hm, "SICKNESS_STATE_PATH", sickness_state)
    emergency_state = tmp_path / "emergency.json"
    monkeypatch.setattr(hm, "EMERGENCY_STATE_PATH", emergency_state)
    # Set recipient explicitly so _send_sickness_email doesn't early-exit
    # in test envs that don't have JARVIS_HEALTH_*_EMAIL_TO set.
    monkeypatch.setattr(hm, "SICKNESS_EMAIL_TO", "test-recipient@example.com")

    cfg_path = tmp_path / "apple_health_remote.json"
    cfg_path.write_text(json.dumps({"base_url": "https://hae.example.test", "read_token": "tok"}))
    monkeypatch.setattr(hm, "_load_config", lambda: json.loads(cfg_path.read_text()))

    sent_messages: list[str] = []
    monkeypatch.setattr(hm.telegram_notify, "send", lambda text: sent_messages.append(text) or True)

    sent_emails: list[dict] = []
    monkeypatch.setattr(
        hm.email_notify,
        "send",
        lambda *, to, subject, body: (
            sent_emails.append({"to": to, "subject": subject, "body": body}) or True
        ),
    )

    monkeypatch.setattr(
        hm,
        "ask_llm_for_message",
        lambda findings, patterns, data_state: "🤧 Early illness signal, sir.",
    )

    # Force detect_all to surface a sickness pattern regardless of what the
    # synthetic HAE response says. This isolates the test to main()'s
    # sickness-routing plumbing.
    fake_sickness = {
        "id": "sickness_signal",
        "severity": "sickness",
        "headline": "Early illness signal — 2 of 6 markers concerning",
        "evidence": {"signals_fired": ["rhr_up", "hrv_down"]},
        "interpretation": "test",
    }
    monkeypatch.setattr(hm.patterns, "detect_all", lambda findings, *, gated_by: [fake_sickness])

    with respx.mock:
        for entry in hm.METRIC_REGISTRY:
            respx.get(f"https://hae.example.test/api/metrics/{entry.id}").mock(
                return_value=Response(200, json=_hae_response(entry.id))
            )
        rc = hm.main()

    assert rc == 0
    # Telegram fired
    assert len(sent_messages) == 1
    # Email mirror fired
    assert len(sent_emails) == 1
    assert "Early illness signal" in sent_emails[0]["subject"]
    # State file persisted active id
    state = json.loads(sickness_state.read_text())
    assert "sickness_signal" in state.get("active_pattern_ids", [])

    # Second run with the same active sickness pattern: edge-trigger
    # should suppress Telegram/email mirror (no new transition).
    sent_messages.clear()
    sent_emails.clear()
    with respx.mock:
        for entry in hm.METRIC_REGISTRY:
            respx.get(f"https://hae.example.test/api/metrics/{entry.id}").mock(
                return_value=Response(200, json=_hae_response(entry.id))
            )
        rc2 = hm.main()
    assert rc2 == 0
    assert sent_messages == []
    assert sent_emails == []


def test_main_returns_2_and_logs_error_on_analyse_failure(tmp_path, monkeypatch):
    """When analyse() raises, main() logs _error to JSONL and returns 2."""
    from jarvis.agents import health_monitor as hm

    log_path = tmp_path / "health-monitor.jsonl"
    monkeypatch.setattr(hm, "JSONL_LOG_PATH", log_path)
    monkeypatch.setattr(hm, "analyse", lambda: (_ for _ in ()).throw(RuntimeError("HAE down")))
    sent_messages = []
    monkeypatch.setattr(hm.telegram_notify, "send", lambda text: sent_messages.append(text) or True)

    rc = hm.main()
    assert rc == 2
    # No telegram on analyse failure
    assert sent_messages == []
    # JSONL got an error entry
    last = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert "_error" in last
    assert "HAE down" in last["_error"]
