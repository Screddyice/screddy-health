# Health Digest v3.2 Implementation Plan

> **Provenance:** This is the implementation plan written for the author's
> original deployment. Paths like `~/jarvis/vendor/openjarvis/`, the
> `neb-server` SSH alias, and the `Screddyice/jarvis` repo refer to the
> private upstream from which this project was extracted. The code in
> this repository has been generalized so a fresh install does not need
> any of that infrastructure — see the project root `README.md` for
> setup. The plan is preserved verbatim as a record of how the feature
> was designed, decomposed, and shipped.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the existing health-monitor on `neb-server` into a twice-weekly narrative health digest (Tue 12:00 PT, Sun 16:00 PT), route LLM generation through Jarvis's `/chat` API, and collapse the every-3h emergency check into a once-daily 11:00 PT watchdog.

**Architecture:** Two systemd timers on `neb-server`. `health-monitor.timer` (daily 11:00 PT) keeps doing the existing silent analyse → JSONL → brain-write → emergency edge-trigger. New `health-digest.timer` (Tue 12:00 + Sun 16:00 PT) runs a new entry point `jarvis.agents.health_digest` that reads JSONL + journal entries, posts a structured prompt to `http://127.0.0.1:8200/chat` (openclaw jarvis agent, GPT-5.4), and delivers via Telegram. A new `_health/glossary.py` module supplies metric definitions injected into the prompt.

**Tech Stack:** Python 3.10+, uv, httpx 0.27+, pytest 8+, respx 0.22+, systemd user units, jarvis-api HTTP server.

**Spec:** [`health-digest-design.md`](health-digest-design.md)

**Branch:** `feat/be-health-digest-v3-2` (in the upstream private repo)

---

## Conventions for every task

- Run tests from the repo root: `cd ~/projects/Screddyice/jarvis && uv run pytest <path> -v`
- All commits sign off with the Co-Authored-By trailer the harness uses.
- Use `git add <specific files>` — never `git add -A` or `git add .`
- Branch naming + commit format follows `~/projects/CLAUDE.md` (Conventional Commits, `feat(scope):` prefix, max 72 char title, body explains why).
- Mock HTTP at the boundary with `respx` (not `unittest.mock`).
- The repo has `requires-python = ">=3.10"` — use modern syntax (`list[dict]`, `str | None`, `dict[str, str]`).

---

## Task 0: Branch sanity check

**Files:**
- Read only.

- [ ] **Step 0.1: Confirm branch + clean tree for the files we'll touch**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git branch --show-current
```
Expected: `feat/be-health-digest-v3-2`

Run:
```bash
cd ~/projects/Screddyice/jarvis && git status --short | grep -vE "^.. openclaw/workspace/" || true
```
Expected: empty (the only WIP is in `openclaw/workspace/` which is unrelated and stays unstaged for the whole plan).

If branch is wrong: `git checkout feat/be-health-digest-v3-2`. If the unrelated workspace files are missing, that's fine — proceed.

---

## Task 1: Create `_health/glossary.py` with `METRIC_GLOSSARY` and `format_for_prompt`

**Files:**
- Create: `src/jarvis/agents/_health/glossary.py`
- Test: `tests/jarvis/agents/_health/test_glossary.py`

### Step 1.1: Write the failing test

Create `tests/jarvis/agents/_health/__init__.py` if missing:

```bash
mkdir -p ~/projects/Screddyice/jarvis/tests/jarvis/agents/_health
touch ~/projects/Screddyice/jarvis/tests/jarvis/agents/_health/__init__.py
```

- [ ] **Step 1.1: Write the failing test for `format_for_prompt`**

Create `tests/jarvis/agents/_health/test_glossary.py`:

```python
"""Tests for the metric glossary used in the digest LLM prompt."""
from __future__ import annotations


def test_metric_glossary_includes_core_metrics():
    """Every metric in METRIC_REGISTRY has a glossary entry."""
    from jarvis.agents._health.glossary import METRIC_GLOSSARY
    from jarvis.agents._health.metric_fetch import METRIC_REGISTRY

    missing = [e.id for e in METRIC_REGISTRY if e.id not in METRIC_GLOSSARY]
    assert missing == [], f"missing glossary entries: {missing}"


def test_glossary_entry_shape():
    """Each glossary entry has label, units, definition, healthy_range, concerning."""
    from jarvis.agents._health.glossary import METRIC_GLOSSARY

    required_keys = {"label", "units", "definition", "healthy_range", "concerning"}
    for metric_id, entry in METRIC_GLOSSARY.items():
        missing = required_keys - set(entry.keys())
        assert missing == set(), f"{metric_id} missing keys: {missing}"


def test_format_for_prompt_includes_only_present_metrics():
    """format_for_prompt renders only metrics that appear in findings AND glossary."""
    from jarvis.agents._health.glossary import format_for_prompt

    findings = {
        "resting_heart_rate": {"recent_mean": 70, "stale": False},
        "heart_rate_variability": {"recent_mean": 45, "stale": False},
        "_data_state": {"current": "watch_on"},  # underscore key — skipped
        "made_up_metric": {"recent_mean": 1.0, "stale": False},  # no glossary entry — skipped
    }
    block = format_for_prompt(findings)

    assert "Resting heart rate" in block
    assert "Heart rate variability" in block
    assert "_data_state" not in block
    assert "made_up_metric" not in block


def test_format_for_prompt_empty_when_no_overlap():
    """format_for_prompt returns empty string when findings have no glossary matches."""
    from jarvis.agents._health.glossary import format_for_prompt

    assert format_for_prompt({"_data_state": {}}) == ""
    assert format_for_prompt({}) == ""
```

- [ ] **Step 1.2: Run the test and confirm it fails**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/_health/test_glossary.py -v
```
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `jarvis.agents._health.glossary`.

- [ ] **Step 1.3: Create `glossary.py` with the implementation**

Create `src/jarvis/agents/_health/glossary.py`:

```python
"""Metric glossary — short, plain-English definitions of every Apple Health
metric tracked by the watchdog. Injected into the digest LLM prompt so the
generated narrative can name what a value means and what a healthy range
looks like.

Kept narrow on purpose. Each entry has five fields:
  label          — human-readable name
  units          — measurement unit
  definition     — one-sentence "what this is"
  healthy_range  — one-sentence reference range / personalization note
  concerning    — one-sentence "this is when to pay attention"
"""
from __future__ import annotations


METRIC_GLOSSARY: dict[str, dict[str, str]] = {
    "step_count": {
        "label": "Step count",
        "units": "steps/day",
        "definition": "Total steps recorded by iPhone + Watch.",
        "healthy_range": "7,000–10,000+ typical for an active adult.",
        "concerning": "Sustained < 3,000/day suggests deconditioning or illness.",
    },
    "active_energy": {
        "label": "Active energy",
        "units": "kcal/day",
        "definition": "Calories burned above resting metabolism.",
        "healthy_range": "300–600 kcal typical for a moderately active day.",
        "concerning": "Sudden 14-day drop > 15% with no schedule change suggests deconditioning or illness.",
    },
    "apple_exercise_time": {
        "label": "Exercise minutes",
        "units": "min/day",
        "definition": "Minutes the Watch logged as brisk-walk-or-harder.",
        "healthy_range": "30+ minutes/day = standard Apple ring close.",
        "concerning": "Acute:chronic ratio < 0.7 or > 1.3 over a week suggests under- or over-training.",
    },
    "resting_heart_rate": {
        "label": "Resting heart rate (RHR)",
        "units": "bpm",
        "definition": "Lowest sustained heart rate observed during rest.",
        "healthy_range": "55–75 bpm typical; lower trends with cardio fitness.",
        "concerning": "Sustained increase of 8–10 bpm from baseline often precedes illness; sustained > 100 bpm at rest is clinically abnormal.",
    },
    "heart_rate_variability": {
        "label": "Heart rate variability (HRV)",
        "units": "ms",
        "definition": "Beat-to-beat variation in heart rate; higher = better autonomic recovery.",
        "healthy_range": "Personalized — your typical range is the only relevant reference.",
        "concerning": "A 20–30% drop sustained over 3+ days signals stress, illness, alcohol, or poor sleep.",
    },
    "sleep_analysis": {
        "label": "Sleep duration",
        "units": "hours/night",
        "definition": "Total time the Watch classified as asleep.",
        "healthy_range": "7–9 hours/night for adults.",
        "concerning": "3+ consecutive nights below 6.5h or a 14-day average drop > 15% is sleep debt.",
    },
    "blood_oxygen_saturation": {
        "label": "Overnight blood oxygen (SpO2)",
        "units": "% saturation",
        "definition": "Lowest overnight reading from the Watch's pulse oximeter.",
        "healthy_range": "95–100% at sea level.",
        "concerning": "2+ consecutive nights below ~92% warrant clinical attention.",
    },
    "respiratory_rate": {
        "label": "Respiratory rate",
        "units": "breaths/min",
        "definition": "Breaths per minute during sleep (Watch-derived).",
        "healthy_range": "12–20 bpm for resting adults.",
        "concerning": "Sustained > 20 bpm with z-score ≥ +2 often precedes respiratory illness.",
    },
    "walking_heart_rate_average": {
        "label": "Walking heart rate average",
        "units": "bpm",
        "definition": "Average heart rate during everyday walking.",
        "healthy_range": "90–115 bpm typical for active adults.",
        "concerning": "Sustained increase with no fitness change can indicate deconditioning or illness.",
    },
    "walking_speed": {
        "label": "Walking speed",
        "units": "m/s",
        "definition": "Average speed during everyday walking.",
        "healthy_range": "1.2–1.4 m/s typical for healthy adults.",
        "concerning": "Sustained decline often correlates with fatigue, joint issues, or general decline.",
    },
    "walking_asymmetry_percentage": {
        "label": "Walking asymmetry",
        "units": "% of steps asymmetric",
        "definition": "Fraction of strides where one leg's timing differs from the other.",
        "healthy_range": "< 3% typical.",
        "concerning": "Sustained > 5% can indicate injury, gait change, or neurological issue.",
    },
    "vo2_max": {
        "label": "Cardio fitness (VO2 max)",
        "units": "mL/kg/min",
        "definition": "Estimated maximum oxygen uptake. Apple updates weekly during workouts.",
        "healthy_range": "Personalized; 40+ = good for adults, 50+ = excellent.",
        "concerning": "Decline > 5% over 90 days suggests deconditioning.",
    },
    "basal_body_temperature": {
        "label": "Wrist temperature deviation",
        "units": "°C from baseline",
        "definition": "Overnight wrist temperature variation from your personal baseline.",
        "healthy_range": "Within ±0.3°C of baseline.",
        "concerning": "Deviation > 0.4°C, especially elevation, can precede illness or reflect hormonal shifts.",
    },
    "apple_walking_steadiness": {
        "label": "Walking steadiness",
        "units": "% steady",
        "definition": "iPhone-derived gait stability rating.",
        "healthy_range": "OK (> 80%) for healthy adults.",
        "concerning": "Sustained drop into Low (< 50%) indicates elevated fall risk.",
    },
    "mindful_session": {
        "label": "Mindful minutes",
        "units": "min/day",
        "definition": "Time logged in Breathe / Mindfulness sessions.",
        "healthy_range": "5–20 min/day if practiced.",
        "concerning": "Not a clinical metric — context for nervous-system state only.",
    },
    "flights_climbed": {
        "label": "Flights climbed",
        "units": "flights/day",
        "definition": "Stair flights ascended (iPhone barometer).",
        "healthy_range": "5+ flights/day suggests good lower-body activity.",
        "concerning": "Sustained collapse from baseline is a deconditioning signal.",
    },
    "distance_walking_running": {
        "label": "Walking/running distance",
        "units": "km/day",
        "definition": "Distance covered on foot from iPhone + Watch.",
        "healthy_range": "3–8 km/day typical for an active adult.",
        "concerning": "Used in cross-check; no single threshold on its own.",
    },
    "apple_stand_time": {
        "label": "Stand minutes",
        "units": "min/day",
        "definition": "Minutes registered as standing-and-moving across the day.",
        "healthy_range": "12+ stand hours/day = Apple ring close.",
        "concerning": "Sustained collapse with desk-bound schedule is a sedentary signal.",
    },
    "irregular_heart_rhythm_event": {
        "label": "Irregular heart rhythm events (AFib)",
        "units": "events",
        "definition": "Watch-detected atrial-fibrillation-suspicious rhythm events.",
        "healthy_range": "0 expected.",
        "concerning": "Any event warrants clinical follow-up; recurrence is emergency-tier.",
    },
    "high_heart_rate_event": {
        "label": "High heart rate alerts",
        "units": "events",
        "definition": "Watch alerts for resting-state HR above its high threshold.",
        "healthy_range": "0 expected.",
        "concerning": "Any event warrants clinical follow-up.",
    },
    "low_heart_rate_event": {
        "label": "Low heart rate alerts",
        "units": "events",
        "definition": "Watch alerts for HR below its low threshold (default 40 bpm).",
        "healthy_range": "0 expected unless an athlete with sustained low HR.",
        "concerning": "Any event with symptoms (dizziness, syncope) warrants emergency evaluation.",
    },
}


def format_for_prompt(findings: dict) -> str:
    """Render a Markdown glossary block covering only the metrics that appear
    in `findings` AND have a glossary entry. Returns "" when there is no
    overlap. Keys starting with `_` (metadata like `_data_state`, `_run_at`)
    are skipped.

    Output shape (Markdown):

        ### Metric glossary (for citations below)
        - **Resting heart rate (RHR)** (bpm) — Lowest sustained heart rate... healthy 55–75. Concerning: sustained increase of 8–10 bpm...
        - **Heart rate variability (HRV)** (ms) — Beat-to-beat variation... Concerning: a 20–30% drop sustained over 3+ days...
    """
    if not findings:
        return ""
    lines: list[str] = []
    for key in findings:
        if key.startswith("_"):
            continue
        entry = METRIC_GLOSSARY.get(key)
        if not entry:
            continue
        lines.append(
            f"- **{entry['label']}** ({entry['units']}) — {entry['definition']} "
            f"Healthy: {entry['healthy_range']} Concerning: {entry['concerning']}"
        )
    if not lines:
        return ""
    return "### Metric glossary (for citations below)\n" + "\n".join(lines)
```

- [ ] **Step 1.4: Run the test and confirm it passes**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/_health/test_glossary.py -v
```
Expected: 4 passed.

- [ ] **Step 1.5: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add \
  src/jarvis/agents/_health/glossary.py \
  tests/jarvis/agents/_health/__init__.py \
  tests/jarvis/agents/_health/test_glossary.py && \
git commit -m "$(cat <<'EOF'
feat(health): add metric glossary for digest LLM prompts

New _health/glossary.py module with METRIC_GLOSSARY covering all 21
metrics in METRIC_REGISTRY, plus format_for_prompt() helper that
renders a Markdown block of only the metrics present in findings.
Injected into the digest prompt so generated narratives can define
metrics inline instead of leaving raw numbers without context.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `_health/jarvis_api_client.py` HTTP wrapper

**Files:**
- Create: `src/jarvis/agents/_health/jarvis_api_client.py`
- Test: `tests/jarvis/agents/_health/test_jarvis_api_client.py`

### Step 2.1: Write the failing test

- [ ] **Step 2.1: Write the failing test**

Create `tests/jarvis/agents/_health/test_jarvis_api_client.py`:

```python
"""Tests for the thin HTTP wrapper around Jarvis's /chat endpoint."""
from __future__ import annotations

import pytest
import respx
from httpx import Response


def test_chat_returns_answer_field(monkeypatch):
    """Successful /chat call returns the `answer` field from the JSON body."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(200, json={"answer": "hello sir", "session_id": "s1"})
        )
        out = jarvis_api_client.chat("Tell me about my week")
    assert out == "hello sir"


def test_chat_sends_authorization_and_message(monkeypatch):
    """The POST body contains the message; Authorization header carries the token."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        route = respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(200, json={"answer": "ok"})
        )
        jarvis_api_client.chat("Tell me about my week")

    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer tok123"
    import json as _json
    body = _json.loads(req.content)
    assert body["message"] == "Tell me about my week"


def test_chat_non_200_raises(monkeypatch):
    """Non-200 response raises with the status code in the message."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(502, text="bad gateway")
        )
        with pytest.raises(jarvis_api_client.JarvisApiError) as excinfo:
            jarvis_api_client.chat("hi")
    assert "502" in str(excinfo.value)


def test_chat_missing_token_raises(monkeypatch):
    """Missing JARVIS_API_TOKEN raises clearly before any network call."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.delenv("JARVIS_API_TOKEN", raising=False)
    with pytest.raises(jarvis_api_client.JarvisApiError) as excinfo:
        jarvis_api_client.chat("hi")
    assert "JARVIS_API_TOKEN" in str(excinfo.value)


def test_chat_passes_session_id_when_provided(monkeypatch):
    """Optional session_id appears in the POST body."""
    from jarvis.agents._health import jarvis_api_client

    monkeypatch.setenv("JARVIS_API_TOKEN", "tok123")
    with respx.mock:
        route = respx.post("http://127.0.0.1:8200/chat").mock(
            return_value=Response(200, json={"answer": "ok"})
        )
        jarvis_api_client.chat("hi", session_id="abc-123")

    import json as _json
    body = _json.loads(route.calls.last.request.content)
    assert body["session_id"] == "abc-123"
```

- [ ] **Step 2.2: Run the test and confirm it fails**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/_health/test_jarvis_api_client.py -v
```
Expected: 5 errors, all `ImportError` for `jarvis_api_client`.

- [ ] **Step 2.3: Implement `jarvis_api_client.py`**

Create `src/jarvis/agents/_health/jarvis_api_client.py`:

```python
"""Thin HTTP client for Jarvis's local /chat endpoint.

The digest pipeline routes ALL narrative generation through Jarvis itself
(http://127.0.0.1:8200/chat → openclaw "jarvis" agent, GPT-5.4 + tools)
rather than instantiating its own OpenAI client. This keeps the digest's
voice consistent with the user's voice/chat surface and lets the digest
benefit from any tool access the agent has.

Auth: JARVIS_API_TOKEN env var is sent as `Authorization: Bearer <token>`.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

JARVIS_API_URL = "http://127.0.0.1:8200/chat"
DEFAULT_TIMEOUT_S = 120.0


class JarvisApiError(RuntimeError):
    """Raised when the Jarvis API call fails (network, non-200, missing creds)."""


def chat(
    message: str,
    *,
    session_id: Optional[str] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    url: str = JARVIS_API_URL,
) -> str:
    """POST `message` to Jarvis /chat and return the answer string.

    Raises JarvisApiError on:
      - Missing JARVIS_API_TOKEN env var
      - Network / transport error (timeout, connection refused)
      - Non-200 response
      - Missing `answer` field in the response body
    """
    token = os.environ.get("JARVIS_API_TOKEN")
    if not token:
        raise JarvisApiError(
            "JARVIS_API_TOKEN is not set; cannot call Jarvis /chat. "
            "Source ~/jarvis/config/jarvis.env or set the env var."
        )

    payload: dict[str, str] = {"message": message}
    if session_id is not None:
        payload["session_id"] = session_id

    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise JarvisApiError(f"Jarvis /chat transport error: {exc}") from exc

    if resp.status_code != 200:
        snippet = resp.text[:200].replace("\n", " ")
        raise JarvisApiError(
            f"Jarvis /chat returned {resp.status_code}: {snippet}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise JarvisApiError(f"Jarvis /chat returned non-JSON: {exc}") from exc

    answer = data.get("answer")
    if not isinstance(answer, str):
        raise JarvisApiError(
            f"Jarvis /chat response missing 'answer' string field: {data}"
        )
    return answer
```

- [ ] **Step 2.4: Run the test and confirm it passes**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/_health/test_jarvis_api_client.py -v
```
Expected: 5 passed.

- [ ] **Step 2.5: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add \
  src/jarvis/agents/_health/jarvis_api_client.py \
  tests/jarvis/agents/_health/test_jarvis_api_client.py && \
git commit -m "$(cat <<'EOF'
feat(health): add Jarvis API HTTP client for digest LLM routing

New _health/jarvis_api_client.py wraps POST http://127.0.0.1:8200/chat
with Bearer token auth, 120s default timeout, and JarvisApiError on
any failure mode. Used by the upcoming health-digest path so narrative
generation flows through Jarvis itself (openclaw jarvis agent, GPT-5.4)
rather than a direct OpenAI instantiation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Rename `retrospective.py` → `weekly_digest.py` and generalize Tue/Sun gate

**Files:**
- Rename: `src/jarvis/agents/_health/retrospective.py` → `src/jarvis/agents/_health/weekly_digest.py`
- Modify (the renamed file)
- Create: `tests/jarvis/agents/_health/test_weekly_digest.py`

### Step 3.1: Rename the file with git mv

- [ ] **Step 3.1: Rename via `git mv` to preserve history**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git mv \
  src/jarvis/agents/_health/retrospective.py \
  src/jarvis/agents/_health/weekly_digest.py
```

### Step 3.2: Write failing tests for the new shape

- [ ] **Step 3.2: Write the failing tests**

Create `tests/jarvis/agents/_health/test_weekly_digest.py`:

```python
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
```

- [ ] **Step 3.3: Run the tests and confirm they fail**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/_health/test_weekly_digest.py -v
```
Expected: 9 failures — most will be `TypeError` (current `run()` signature doesn't accept `chat_fn` / `load_journal`) or the day gate rejecting Tuesday.

### Step 3.4: Rewrite weekly_digest.py

- [ ] **Step 3.4: Replace `weekly_digest.py` with the new implementation**

Replace the entire contents of `src/jarvis/agents/_health/weekly_digest.py` with:

```python
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
    except Exception as exc:
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
    today_signal: dict[str, dict] = {}
    for metric, blob in todays_findings.items():
        if metric.startswith("_") or not isinstance(blob, dict):
            continue
        today_signal[metric] = {k: v for k, v in blob.items() if k in signal_keys}
    today_json = json.dumps(today_signal, indent=2, default=str)

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
        "If the past 7 days are mostly watch_off / partial on the physical side, lead with "
        "that. If journal entries are sparse (<2 in the window), say so explicitly in "
        "_Inner state_ rather than fabricating analysis.\n\n"
        f"{glossary_block}\n\n"
        f"Last 7 days physical rollup:\n{week_json}\n\n"
        f"Today's enriched per-metric signal:\n{today_json}\n\n"
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
```

- [ ] **Step 3.5: Run the tests and confirm they pass**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/_health/test_weekly_digest.py -v
```
Expected: 9 passed.

- [ ] **Step 3.6: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add \
  src/jarvis/agents/_health/weekly_digest.py \
  tests/jarvis/agents/_health/test_weekly_digest.py && \
git commit -m "$(cat <<'EOF'
feat(health): generalize retrospective → weekly_digest (Tue+Sun gate)

- Rename _health/retrospective.py → _health/weekly_digest.py (preserves
  history via git mv)
- Replace Sunday-only gate (SUNDAY=6) with DIGEST_DAYS = {1, 6} for
  Tuesday + Sunday delivery
- Distinct headline + framing per day (Midweek check-in vs Sunday briefing)
- Inject metric glossary block so generated narrative defines cited
  values inline
- Replace direct OpenAI client injection with chat_fn + load_journal
  callables (default to jarvis_api_client.chat + brain helper); tests
  pass stubs in
- Swallow chat-call exceptions in run() so a Jarvis API hiccup yields
  None (skipped fire) rather than crashing the caller

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Create `agents/health_digest.py` entry point

**Files:**
- Create: `src/jarvis/agents/health_digest.py`
- Test: `tests/jarvis/agents/test_health_digest.py`

### Step 4.1: Write the failing tests

- [ ] **Step 4.1: Write the failing tests**

Create `tests/jarvis/agents/test_health_digest.py`:

```python
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
```

- [ ] **Step 4.2: Run the tests and confirm they fail**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/test_health_digest.py -v
```
Expected: 5 errors (`ModuleNotFoundError: No module named 'jarvis.agents.health_digest'`).

### Step 4.3: Implement the entry point

- [ ] **Step 4.3: Create `health_digest.py`**

Create `src/jarvis/agents/health_digest.py`:

```python
"""health_digest — twice-weekly narrative briefing entry point.

Fires Tue 12:00 PT and Sun 16:00 PT via systemd timer health-digest.timer.
Reads the existing JSONL audit log + the past week of mental-health journal
entries, routes generation through Jarvis's /chat API (openclaw jarvis
agent, GPT-5.4), and delivers the result to Telegram.

Daily silent analysis (analyse → JSONL → brain write → emergency
edge-trigger) is owned by jarvis.agents.health_monitor and runs on its
own timer. This module does NOT call analyse(); it consumes whatever the
daily watchdog has already written.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jarvis.agents._health import weekly_digest
from jarvis.channels import telegram_notify

logger = logging.getLogger(__name__)

JSONL_LOG_PATH = Path("/home/ubuntu/logs/health-monitor.jsonl")
LA_TZ = ZoneInfo("America/Los_Angeles")

DISCLAIMER = (
    "_Pattern check, not a diagnosis. Talk to a clinician for medical concerns._"
)


def _current_weekday_in_la() -> int:
    """Weekday in America/Los_Angeles (Mon=0..Sun=6)."""
    return datetime.now(timezone.utc).astimezone(LA_TZ).weekday()


def _load_prior_jsonl(limit_lines: int = 30) -> list[dict]:
    """Read up to `limit_lines` tail entries from the JSONL log."""
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


def main() -> int:
    today_weekday = _current_weekday_in_la()
    if today_weekday not in weekly_digest.DIGEST_DAYS:
        # Defensive — the timer should already enforce this.
        return 0

    prior = _load_prior_jsonl(limit_lines=30)
    todays_findings = prior[-1] if prior else {}

    if not prior:
        # No health data at all — send a short notice so the user knows
        # the watchdog hasn't been writing.
        msg = (
            "_Digest skipped, sir — no health data in the window. "
            "Check the daily watchdog._\n\n" + DISCLAIMER
        )
        ok = telegram_notify.send(msg)
        return 0 if ok else 1

    answer = weekly_digest.run(
        prior_30d_jsonl=prior,
        todays_findings=todays_findings,
        today_weekday=today_weekday,
    )

    if not answer:
        # The engine returned None — either chat failed or returned empty.
        # Surface this to the user rather than going silent.
        fallback = (
            "_Digest skipped, sir — Jarvis didn't respond. "
            "Will retry on next scheduled fire._\n\n" + DISCLAIMER
        )
        ok = telegram_notify.send(fallback)
        return 1  # always exit 1 here; a digest day with no output is a problem

    message = answer + "\n\n" + DISCLAIMER
    ok = telegram_notify.send(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4.4: Run the tests and confirm they pass**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/test_health_digest.py -v
```
Expected: 5 passed.

- [ ] **Step 4.5: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add \
  src/jarvis/agents/health_digest.py \
  tests/jarvis/agents/test_health_digest.py && \
git commit -m "$(cat <<'EOF'
feat(health): add health_digest entry point for Tue+Sun delivery

New jarvis.agents.health_digest module is the systemd-invoked entry
point for the twice-weekly digest. Reads the JSONL log, defers to
weekly_digest.run() for narrative generation, dispatches via
telegram_notify. Handles three off-nominal cases explicitly:
  - non-digest weekday → exit 0, silent
  - empty JSONL → short "no data" notice, exit 0/1 on delivery
  - engine returned None (chat failed) → "skipped" notice, exit 1
Weekday is resolved in America/Los_Angeles so DST shifts don't
change behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Strip the Sunday retrospective block from `health_monitor.py`

**Files:**
- Modify: `src/jarvis/agents/health_monitor.py`
- Modify: `tests/jarvis/agents/test_health_monitor.py` (verify no breakage)

The retrospective lives in `health_digest.py` now. The daily watchdog should *only* do analysis + emergency edge-trigger.

### Step 5.1: Read and edit `health_monitor.py`

- [ ] **Step 5.1: Remove the retrospective import**

In `src/jarvis/agents/health_monitor.py`, change line 18:

**Old:**
```python
from jarvis.agents._health import data_state, metric_fetch, patterns, retrospective
```

**New:**
```python
from jarvis.agents._health import data_state, metric_fetch, patterns
```

- [ ] **Step 5.2: Remove the Sunday retrospective block**

In `src/jarvis/agents/health_monitor.py`, find this block (currently around lines 389-402):

**Old:**
```python
    # Sunday retrospective
    today_weekday = datetime.now(timezone.utc).weekday()
    retro_msg = None
    if today_weekday == retrospective.SUNDAY:
        try:
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        except Exception:
            client = None
        retro_msg = retrospective.run(
            prior_30d_jsonl=_load_prior_jsonl(limit_lines=30),
            todays_findings=findings,
            today_weekday=today_weekday,
            llm_client=client,
        )

    # Deliver
    unsuppressed = [p for p in fired if not p.get("suppressed_by")]
```

**New (replace the entire block above with just the `# Deliver` block):**
```python
    # Deliver
    unsuppressed = [p for p in fired if not p.get("suppressed_by")]
```

- [ ] **Step 5.3: Remove `retro_msg` references in the deliver block**

In the same file, find the deliver block (currently around lines 414-431). The `retro_msg` variable no longer exists, so its conditional and concatenation must go.

**Old:**
```python
    # If only thing fresh is an already-active emergency (no new alert) AND
    # nothing else is in play, stay silent.
    if not non_emergency and not retro_msg and not emergency_to_alert:
        return 0

    parts: list[str] = []
    # Emergency takes the lead in the LLM message if present.
    llm_input = (emergency_to_alert + non_emergency) if emergency_to_alert else non_emergency
    if llm_input:
        try:
            parts.append(ask_llm_for_message(enriched, llm_input, state))
        except Exception as exc:
            parts.append(f"Health pattern detected, but LLM rendering failed: {exc}")
    if retro_msg:
        parts.append(retro_msg)
    parts.append(DISCLAIMER)
```

**New:**
```python
    # If only thing fresh is an already-active emergency (no new alert) AND
    # nothing else is in play, stay silent.
    if not non_emergency and not emergency_to_alert:
        return 0

    parts: list[str] = []
    # Emergency takes the lead in the LLM message if present.
    llm_input = (emergency_to_alert + non_emergency) if emergency_to_alert else non_emergency
    if llm_input:
        try:
            parts.append(ask_llm_for_message(enriched, llm_input, state))
        except Exception as exc:
            parts.append(f"Health pattern detected, but LLM rendering failed: {exc}")
    parts.append(DISCLAIMER)
```

- [ ] **Step 5.4: Run the existing health_monitor tests**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/test_health_monitor.py -v
```
Expected: 3 passed (the three existing tests don't depend on the retrospective block; the came-back / anti-spam / analyse-failure paths all return before the deleted code or after it without touching it).

If any test fails because of a reference to `retrospective` it didn't exist before — investigate, fix the test if it was incorrect, but the three documented tests above should pass cleanly.

- [ ] **Step 5.5: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add src/jarvis/agents/health_monitor.py && \
git commit -m "$(cat <<'EOF'
refactor(health): move Sunday retrospective out of health_monitor

The retrospective / digest path now lives in jarvis.agents.health_digest,
invoked by its own systemd timer twice a week. The daily watchdog
(health_monitor) is responsible only for the silent analysis →
JSONL → brain write → emergency edge-trigger chain. Remove the
retrospective import and the Sunday-only block, plus the retro_msg
plumbing in the deliver path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Delete `agents/emergency_check.py`

**Files:**
- Delete: `src/jarvis/agents/emergency_check.py`

With the 3h timer collapsed into the daily 11:00 PT run, `emergency_check.py` has no caller.

- [ ] **Step 6.1: Confirm no other file imports it**

Run:
```bash
cd ~/projects/Screddyice/jarvis && grep -rn "emergency_check" src/ tests/ deploy/ 2>/dev/null | grep -v __pycache__ | grep -v ".pyc"
```
Expected: only matches inside `src/jarvis/agents/emergency_check.py` itself (and possibly its own docstring). If anything else references it (test file, import elsewhere), STOP and investigate before deleting.

- [ ] **Step 6.2: Delete the file**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git rm src/jarvis/agents/emergency_check.py
```

- [ ] **Step 6.3: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git commit -m "$(cat <<'EOF'
refactor(health): remove emergency_check (folded into daily watchdog)

The every-3h emergency_check existed because the watchdog only ran
Sundays. The watchdog now runs daily at 11:00 PT, after Apple Health's
morning sync — same coverage, no separate module needed. Delete
emergency_check.py and its associated systemd units (next commit).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update `health-monitor.timer` to daily 11:00 PT

**Files:**
- Modify: `deploy/neb/systemd/health-monitor.timer`

- [ ] **Step 7.1: Replace the timer file contents**

Replace `deploy/neb/systemd/health-monitor.timer` with:

```ini
[Unit]
Description=Daily Jarvis health watchdog — Apple Health analysis + emergency edge-trigger
Requires=health-monitor.service

[Timer]
# Daily at 11:00 PT (America/Los_Angeles) — after Apple Health's morning
# sync has settled. Replaces the prior Sunday-only 12:00 UTC schedule.
# The twice-weekly narrative digest runs separately via
# health-digest.timer (Tue 12:00 + Sun 16:00 PT).
OnCalendar=*-*-* 11:00:00 America/Los_Angeles
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 7.2: Update the service Description (optional clarity tweak)**

In `deploy/neb/systemd/health-monitor.service`, change line 2:

**Old:**
```
Description=Jarvis health monitor — daily anomaly check on Apple Health data
```

**New:**
```
Description=Jarvis health watchdog — daily Apple Health analysis + emergency edge-trigger
```

(Cosmetic. Skip if it errors for any reason — the service body is what matters.)

- [ ] **Step 7.3: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add \
  deploy/neb/systemd/health-monitor.timer \
  deploy/neb/systemd/health-monitor.service && \
git commit -m "$(cat <<'EOF'
chore(deploy): switch health-monitor to daily 11:00 PT schedule

The watchdog now fires daily in America/Los_Angeles after Apple Health
finishes its morning sync. Replaces the prior weekly Sunday 12:00 UTC
schedule. Using a named timezone (not UTC) so DST shifts don't change
the user-visible run time. Service description updated to reflect the
new role (watchdog + emergency edge-trigger, not just anomaly check).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Create `health-digest.timer` + `health-digest.service`

**Files:**
- Create: `deploy/neb/systemd/health-digest.service`
- Create: `deploy/neb/systemd/health-digest.timer`

- [ ] **Step 8.1: Create the service unit**

Create `deploy/neb/systemd/health-digest.service`:

```ini
[Unit]
Description=Jarvis health digest — twice-weekly narrative briefing via Jarvis /chat
After=network.target jarvis-api.service
Requires=jarvis-api.service

[Service]
Type=oneshot
EnvironmentFile=/home/ubuntu/jarvis/config/jarvis.env
WorkingDirectory=/home/ubuntu/jarvis/vendor/openjarvis
ExecStart=/home/ubuntu/.local/bin/uv run python -m jarvis.agents.health_digest
StandardOutput=append:/home/ubuntu/logs/health-digest.log
StandardError=append:/home/ubuntu/logs/health-digest.log

[Install]
WantedBy=default.target
```

- [ ] **Step 8.2: Create the timer unit**

Create `deploy/neb/systemd/health-digest.timer`:

```ini
[Unit]
Description=Twice-weekly Jarvis health digest — Tue 12:00 + Sun 16:00 PT
Requires=health-digest.service

[Timer]
OnCalendar=Tue 12:00:00 America/Los_Angeles
OnCalendar=Sun 16:00:00 America/Los_Angeles
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 8.3: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git add \
  deploy/neb/systemd/health-digest.service \
  deploy/neb/systemd/health-digest.timer && \
git commit -m "$(cat <<'EOF'
chore(deploy): add health-digest systemd unit (Tue 12:00 + Sun 16:00 PT)

New oneshot service runs `python -m jarvis.agents.health_digest` twice
weekly. Requires=jarvis-api.service ensures the local /chat endpoint
is up before the digest tries to call it. Logs to
~/logs/health-digest.log on neb-server.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Delete the `health-emergency-check.{service,timer}` unit files

**Files:**
- Delete: `deploy/neb/systemd/health-emergency-check.service`
- Delete: `deploy/neb/systemd/health-emergency-check.timer`

- [ ] **Step 9.1: Delete both unit files**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git rm \
  deploy/neb/systemd/health-emergency-check.service \
  deploy/neb/systemd/health-emergency-check.timer
```

- [ ] **Step 9.2: Commit**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git commit -m "$(cat <<'EOF'
chore(deploy): remove health-emergency-check systemd units

Apple Health data refreshes once per day in the morning. The every-3h
emergency check was wasted cycles — the daily 11:00 PT watchdog now
runs the same emergency edge-trigger logic. Removing the units from
the repo; the live deploy step (separate runbook) disables them on
neb-server with systemctl --user.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Final cross-cutting test pass

**Files:**
- Read only.

- [ ] **Step 10.1: Run the entire agent test suite**

Run:
```bash
cd ~/projects/Screddyice/jarvis && uv run pytest tests/jarvis/agents/ -v
```
Expected: all green. New tests:
- `tests/jarvis/agents/_health/test_glossary.py` (4 tests)
- `tests/jarvis/agents/_health/test_jarvis_api_client.py` (5 tests)
- `tests/jarvis/agents/_health/test_weekly_digest.py` (9 tests)
- `tests/jarvis/agents/test_health_digest.py` (5 tests)

Existing tests:
- `tests/jarvis/agents/test_health_monitor.py` (3 tests)
- `tests/jarvis/agents/test_pipeline_watchdog.py` (existing — should be unaffected)

Total new: 23. Combined with existing tests: should be ~26+.

If anything fails, stop and fix it before moving on — no shipping a red suite.

- [ ] **Step 10.2: Confirm no stale references**

Run:
```bash
cd ~/projects/Screddyice/jarvis && grep -rn "retrospective" src/ tests/ deploy/ 2>/dev/null | grep -v __pycache__
```
Expected: NO matches. If any remain, replace with `weekly_digest`.

Run:
```bash
cd ~/projects/Screddyice/jarvis && grep -rn "emergency_check" src/ tests/ deploy/ 2>/dev/null | grep -v __pycache__
```
Expected: NO matches.

- [ ] **Step 10.3: Push the branch**

Run:
```bash
cd ~/projects/Screddyice/jarvis && git push -u origin feat/be-health-digest-v3-2
```
Expected: branch pushed, no errors.

---

## Task 11: Deployment runbook (manual steps on neb-server)

**Files:**
- Read only — these are operational steps Shawn or the agent runs on neb-server, not git edits.

This task does NOT commit to git. It's the post-merge deploy. Run after the PR is merged to `main`.

### Step 11.1: Pull on neb-server

- [ ] **Step 11.1: SSH and pull**

Run:
```bash
ssh neb-server 'cd ~/jarvis && git pull && /home/ubuntu/.local/bin/uv sync'
```
Expected: clean pull, `uv sync` reports either "no changes" or a small dep resolution.

### Step 11.2: Stop + disable the old emergency-check units

- [ ] **Step 11.2: Stop + disable**

Run:
```bash
ssh neb-server 'systemctl --user stop health-emergency-check.timer health-emergency-check.service; systemctl --user disable health-emergency-check.timer health-emergency-check.service'
```
Expected: timer + service stopped and disabled. `systemctl --user list-timers --all | grep emergency` returns empty.

### Step 11.3: Remove the obsolete unit files from the user systemd dir

- [ ] **Step 11.3: Delete the old unit files**

Run:
```bash
ssh neb-server 'rm -f ~/.config/systemd/user/health-emergency-check.service ~/.config/systemd/user/health-emergency-check.timer'
```
Expected: silent success.

### Step 11.4: Install the new + updated unit files

- [ ] **Step 11.4: Copy new units into place**

Run:
```bash
ssh neb-server 'cp ~/jarvis/vendor/openjarvis/deploy/neb/systemd/health-monitor.timer ~/.config/systemd/user/; cp ~/jarvis/vendor/openjarvis/deploy/neb/systemd/health-monitor.service ~/.config/systemd/user/; cp ~/jarvis/vendor/openjarvis/deploy/neb/systemd/health-digest.timer ~/.config/systemd/user/; cp ~/jarvis/vendor/openjarvis/deploy/neb/systemd/health-digest.service ~/.config/systemd/user/'
```
Expected: 4 file copies succeed silently.

> **If the vendor path differs:** the deploy paths in this plan assume the live install lives at `/home/ubuntu/jarvis/vendor/openjarvis/`. If neb-server uses a different install layout, replace the source paths with the actual repo location (`git -C ~/jarvis remote -v` will show whether the repo IS the install or whether the install is vendored).

### Step 11.5: Reload + enable

- [ ] **Step 11.5: Reload + enable the digest timer; restart the monitor timer**

Run:
```bash
ssh neb-server 'systemctl --user daemon-reload && systemctl --user enable --now health-digest.timer && systemctl --user restart health-monitor.timer'
```
Expected: `health-digest.timer` enabled + active; `health-monitor.timer` restarted with the new daily schedule.

### Step 11.6: Verify

- [ ] **Step 11.6: Confirm timers and their next-fire times**

Run:
```bash
ssh neb-server 'systemctl --user list-timers --all | grep -E "health|jarvis"'
```
Expected output should include:
- `health-monitor.timer` with next fire at the next 11:00 PT (in UTC, that's 18:00 or 19:00 depending on DST)
- `health-digest.timer` with next fire at the next Tue 12:00 PT or Sun 16:00 PT, whichever comes first
- NO `health-emergency-check.timer` entry

### Step 11.7: Smoke-test the digest manually

- [ ] **Step 11.7: Trigger the digest once now**

Run:
```bash
ssh neb-server 'systemctl --user start health-digest.service'
```

Then check the log:
```bash
ssh neb-server 'tail -50 ~/logs/health-digest.log'
```
Expected: clean execution, no traceback. Telegram should deliver a digest (or a "no health data in the window" notice if the JSONL is empty / today_weekday isn't a digest day — in which case the smoke test below is more direct).

If today is NOT Tue or Sun, the entry point exits 0 silently. To smoke-test the full flow anyway, you can temporarily override the day check in the running module by running a one-shot:

```bash
ssh neb-server 'cd ~/jarvis/vendor/openjarvis && /home/ubuntu/.local/bin/uv run python -c "
from jarvis.agents._health import weekly_digest, jarvis_api_client
from jarvis import brain
from jarvis.channels import telegram_notify

# Simulate Tuesday
out = weekly_digest.run(
    prior_30d_jsonl=[],  # or load from JSONL
    todays_findings={},
    today_weekday=1,
)
print(out)
"'
```
Expected: a Telegram-formatted digest string printed to stdout (no actual Telegram send), or a clear stack trace if the Jarvis API isn't reachable.

### Step 11.8: Confirm health-monitor still fires nightly

- [ ] **Step 11.8: Manually trigger the daily watchdog**

Run:
```bash
ssh neb-server 'systemctl --user start health-monitor.service && tail -30 ~/logs/health-monitor.log'
```
Expected: clean run, JSONL written, no traceback. Within a few seconds the log should show the new `_run_at` entry being appended to `~/logs/health-monitor.jsonl`.

---

## Done

Once Task 11 is complete:
- Daily 11:00 PT watchdog is silently running.
- Tue 12:00 PT and Sun 16:00 PT digests will land in Telegram, each citing real numbers + journal phrases, with metric definitions woven in.
- Emergency-tier patterns (cardiac event, severe respiratory, systemic inflammation) still trigger Telegram + email immediately upon transition from inactive to active, with the existing "If symptomatic now / If asymptomatic" doctor-contact framing.

Open the PR (`gh pr create`) with a body that points at the spec at `docs/superpowers/specs/2026-05-21-health-digest-v3.2-design.md` and lists the merge-time deployment steps (Task 11).
