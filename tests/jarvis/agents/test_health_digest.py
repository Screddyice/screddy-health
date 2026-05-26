"""Integration tests for the health_digest entry point (Tue 12:00 + Sun 16:00 PT)."""
from __future__ import annotations

import json


def test_main_delivers_telegram_on_tuesday(tmp_path, monkeypatch):
    """Tuesday run with a populated JSONL log dispatches a Telegram message."""
    from jarvis.agents import health_digest as hd

    log_path = tmp_path / "health-monitor.jsonl"
    log_path.write_text(json.dumps({
        "_run_at": "2026-05-19T18:00:00Z",
        "_data_state": {"current": "watch_on"},
        "resting_heart_rate": {"recent_mean": 70, "stale": False},
    }) + "\n")
    monkeypatch.setattr(hd, "JSONL_LOG_PATH", log_path)

    # Force the entry point to think today is Tuesday.
    monkeypatch.setattr(hd, "_current_weekday_in_la", lambda: 1)

    monkeypatch.setattr(
        hd.weekly_digest,
        "run",
        lambda **kwargs: "*Midweek check-in, sir.*\n\nNumbers look fine.",
    )

    sent: list[str] = []
    monkeypatch.setattr(hd.telegram_notify, "send", lambda text: sent.append(text) or True)

    rc = hd.main()
    assert rc == 0
    assert len(sent) == 1
    assert "Midweek check-in" in sent[0]
    # Disclaimer footer present
    assert "Pattern check" in sent[0]


def test_main_returns_zero_on_non_digest_day(tmp_path, monkeypatch):
    """When weekday is not Tue/Sun, exit 0 and send nothing."""
    from jarvis.agents import health_digest as hd

    log_path = tmp_path / "health-monitor.jsonl"
    log_path.write_text(json.dumps({"_run_at": "2026-05-20T18:00:00Z"}) + "\n")
    monkeypatch.setattr(hd, "JSONL_LOG_PATH", log_path)
    monkeypatch.setattr(hd, "_current_weekday_in_la", lambda: 2)  # Wednesday

    sent: list[str] = []
    monkeypatch.setattr(hd.telegram_notify, "send", lambda text: sent.append(text) or True)

    rc = hd.main()
    assert rc == 0
    assert sent == []


def test_main_handles_empty_jsonl(tmp_path, monkeypatch):
    """Empty JSONL on a digest day still dispatches a short notice."""
    from jarvis.agents import health_digest as hd

    log_path = tmp_path / "health-monitor.jsonl"  # never written
    monkeypatch.setattr(hd, "JSONL_LOG_PATH", log_path)
    monkeypatch.setattr(hd, "_current_weekday_in_la", lambda: 6)  # Sunday
    # weekly_digest.run won't be reached on the empty-JSONL branch, but stub
    # for safety in case the branch changes.
    monkeypatch.setattr(hd.weekly_digest, "run", lambda **kwargs: None)

    sent: list[str] = []
    monkeypatch.setattr(hd.telegram_notify, "send", lambda text: sent.append(text) or True)

    rc = hd.main()
    assert rc == 0
    assert len(sent) == 1
    assert "no health data" in sent[0].lower()


def test_main_returns_one_when_telegram_fails(tmp_path, monkeypatch):
    """Telegram delivery failure surfaces as exit 1."""
    from jarvis.agents import health_digest as hd

    log_path = tmp_path / "health-monitor.jsonl"
    log_path.write_text(json.dumps({"_run_at": "2026-05-19T18:00:00Z"}) + "\n")
    monkeypatch.setattr(hd, "JSONL_LOG_PATH", log_path)
    monkeypatch.setattr(hd, "_current_weekday_in_la", lambda: 1)
    monkeypatch.setattr(hd.weekly_digest, "run", lambda **kwargs: "*Midweek check-in, sir.*")
    monkeypatch.setattr(hd.telegram_notify, "send", lambda text: False)

    rc = hd.main()
    assert rc == 1


def test_main_sends_fallback_when_chat_returns_none(tmp_path, monkeypatch):
    """When weekly_digest.run() returns None on a digest day (chat failed
    or empty response), send a short fallback Telegram so the user knows."""
    from jarvis.agents import health_digest as hd

    log_path = tmp_path / "health-monitor.jsonl"
    log_path.write_text(json.dumps({"_run_at": "2026-05-19T18:00:00Z"}) + "\n")
    monkeypatch.setattr(hd, "JSONL_LOG_PATH", log_path)
    monkeypatch.setattr(hd, "_current_weekday_in_la", lambda: 6)
    monkeypatch.setattr(hd.weekly_digest, "run", lambda **kwargs: None)

    sent: list[str] = []
    monkeypatch.setattr(hd.telegram_notify, "send", lambda text: sent.append(text) or True)

    rc = hd.main()
    assert rc == 1  # we treat this as a delivery anomaly
    assert len(sent) == 1
    assert "digest skipped" in sent[0].lower()
