# Health Digest v3.2 — Design Spec

> **Provenance:** This spec was written for the author's personal Jarvis
> deployment on an AWS EC2 instance referred to as `neb-server`. Path
> references like `/home/ubuntu/jarvis/`, deployment identifiers, and
> contact emails reflect that original context. The code published in
> this repository has been generalized — paths, recipients, and
> timezones are configurable via environment variables (see the project
> root `README.md`). Read this document as a snapshot of how the work
> was designed and shipped, not as a deployment manual for a fresh
> install.

**Date:** 2026-05-21
**Implementation target:** `~/jarvis/` on the author's server (referred to as `neb-server` throughout)
**Branch:** `feat/be-health-digest-v3-2`

---

## Goal

Evolve the existing health-monitor (v2 / v3.1) on neb-server into a twice-weekly *health digest* delivered Tuesdays at 12:00 PT and Sundays at 16:00 PT. The digest synthesizes the past week of Apple Health metrics and journal entries into a narrative briefing that names patterns, gives concrete recommendations, and cites specific numbers and journal phrases. Route all narrative generation through the existing Jarvis API (`POST /chat` → openclaw "jarvis" agent, GPT-5.4) so the digest speaks in the same voice as voice/chat.

In parallel, simplify the urgent-monitor path: collapse the every-3h emergency check into a once-daily morning watchdog, matching the cadence at which Apple Health data actually refreshes. Preserve the existing edge-triggered emergency-tier alerts (cardiac, severe respiratory, systemic inflammation) with their "If symptomatic now / If asymptomatic" doctor-contact framing.

## Background

The v2 / v3.1 system already on neb-server does most of what is needed:

- 16-pattern rule detector in `_health/patterns.py`, including three emergency-tier patterns
- Watch-on/off state classification, gap-day counting, post-resume baseline rebuild window
- 3-day anti-spam cooldown
- JSONL audit log at `/home/ubuntu/logs/health-monitor.jsonl`
- Edge-triggered emergency alerts via `~/jarvis/state/emergency.json`, mirrored to email
- Sunday-only journal-aware LLM briefing in `_health/retrospective.py`, pulling entries via `brain.load_recent_mental_health_entries()`

What is missing relative to the new ask:

1. Tuesday delivery cadence (currently Sunday only)
2. Sunday delivery at 16:00 PT (currently 12:00 UTC = 05:00 PT)
3. LLM routed through Jarvis itself, not a direct `OpenAI()` instantiation
4. Metric glossary (definitions, baselines, concerning ranges) included so digest text is self-explanatory
5. Once-daily morning watchdog instead of every-3h emergency check (data only refreshes once a day)

## Non-goals

- Re-implementing the rule-based pattern detector. v2's 16 patterns + emergency tiering stay as-is.
- Replacing the JSONL log format. New code reads the existing schema.
- Changing the urgent-alert framing wording (`If symptomatic now: seek emergency care, sir.` / `If asymptomatic: schedule clinical evaluation within 48 hours.`). Already meets the bar.
- Wiring journal entries into the urgent path. Apple Journal exports run weekly via the Sunday 03:00 PT cron, so journal data has at most ~1-week lag — not useful for "alert me now" urgency. Urgent path remains health-metric-driven.
- Swapping the underlying LLM provider for the rule-pattern alert renderer (`ask_llm_for_message` in `health_monitor.py`). That stays on gpt-4o-mini via direct OpenAI for now — narrow scope to the digest path.
- Migrating historical JSONL entries. New code tolerates the existing schema and appends new entries on top.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  Timer: health-monitor.timer                                           │
│  Schedule: daily 11:00 America/Los_Angeles                             │
│                              │                                         │
│                              ▼                                         │
│           ┌────────────────────────────────────┐                       │
│           │  jarvis.agents.health_monitor      │                       │
│           │  - analyse() (16 patterns)         │                       │
│           │  - write JSONL                     │                       │
│           │  - brain.write_health_metrics()    │                       │
│           │  - edge-triggered emergency alert  │                       │
│           └────────────────────────────────────┘                       │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  Timer: health-digest.timer (new)                                      │
│  Schedule: Tue 12:00 + Sun 16:00 America/Los_Angeles                   │
│                              │                                         │
│                              ▼                                         │
│           ┌────────────────────────────────────┐                       │
│           │  jarvis.agents.health_digest       │                       │
│           │  - load last 7d JSONL + journal    │                       │
│           │  - inject glossary                 │                       │
│           │  - POST 127.0.0.1:8200/chat        │  → openclaw jarvis    │
│           │  - deliver via telegram_notify     │     (GPT-5.4 + tools) │
│           └────────────────────────────────────┘                       │
└────────────────────────────────────────────────────────────────────────┘
```

### File layout (post-change)

```
src/jarvis/agents/
  health_monitor.py             # daily watchdog (modified — retro block removed)
  health_digest.py              # NEW — twice-weekly digest entry point
  emergency_check.py            # DELETED
  _health/
    __init__.py
    metric_fetch.py             # unchanged
    data_state.py               # unchanged
    patterns.py                 # unchanged
    weekly_digest.py            # RENAMED from retrospective.py + generalized day gate
    jarvis_api_client.py        # NEW — HTTP client for Jarvis API /chat
    glossary.py                 # NEW — canonical metric definitions
deploy/neb/systemd/
  health-monitor.timer          # MODIFIED — daily 06:30 LA
  health-monitor.service        # unchanged
  health-digest.timer           # NEW
  health-digest.service         # NEW
  health-emergency-check.timer  # DELETED
  health-emergency-check.service # DELETED
tests/jarvis/agents/
  test_health_monitor.py        # MODIFIED — retro tests moved out
  test_weekly_digest.py         # NEW — Tue/Sun gate + glossary + Jarvis client mock
  test_health_digest.py         # NEW — entry-point integration
```

## Schedules

| Timer | OnCalendar | Cadence | Purpose |
|---|---|---|---|
| `health-monitor.timer` | `*-*-* 11:00:00 America/Los_Angeles` | Daily | Silent analysis + emergency edge-trigger |
| `health-digest.timer` | `Tue 12:00:00 America/Los_Angeles` + `Sun 16:00:00 America/Los_Angeles` | Twice weekly | LLM narrative briefing |

`America/Los_Angeles` (not UTC) is intentional — systemd handles DST, so the user sees 11:00 / 12:00 / 16:00 local year-round.

The 11:00 PT watchdog time is chosen because Apple Health data has finished syncing to the HAE server by mid-morning — sleep_analysis writes after wake, vitals trickle in through the iPhone's overnight + morning sync cycle. Both digest fires (Tue 12:00, Sun 16:00) run *after* the 11:00 watchdog so each digest reads JSONL with the same-day morning entry included — no data lag on either day.

## Code changes

### `_health/weekly_digest.py` (renamed from `retrospective.py`)

Existing logic is largely preserved. Three changes:

1. **Day gate** — replace `SUNDAY = 6 ... if today_weekday != SUNDAY: return None` with `DIGEST_DAYS = {1, 6}` (Tue, Sun) check. Caller (`health_digest.py`) is responsible for invoking only on those days, but the function double-checks for safety.

2. **Tue vs Sun framing** — the structured prompt template is shared, but the headline + framing line differs:
   - **Tuesday:** `*Midweek check-in, sir.*` + framing line: "Past 7 days through this morning — here's how things are trending and what to focus on through the rest of the week."
   - **Sunday:** `*Sunday briefing, sir.*` + framing line: "Week in the books — here's the read, and what to set up for the week ahead."

3. **Glossary injection** — the prompt receives a new `glossary_block` parameter (formatted from `glossary.py`) listing the definition + baseline + concerning ranges for every metric mentioned in `todays_findings`. Instruction added to the prompt: "When you cite a metric value, also briefly state what it means and what a healthy range looks like, drawing from the glossary block below."

LLM call site changes from `llm_client.chat.completions.create(model="gpt-4o-mini", ...)` to `jarvis_api_client.chat(message=prompt, timeout_s=120)`.

### `_health/jarvis_api_client.py` (new)

Thin HTTP wrapper around the Jarvis API:

```python
def chat(message: str, *, session_id: str | None = None, timeout_s: int = 120) -> str:
    """POST to http://127.0.0.1:8200/chat. Returns the answer string.
    Raises on non-200 or transport error."""
```

Authorization: reads `JARVIS_API_TOKEN` from environment (already present in `~/jarvis/config/jarvis.env`). Returns the `"answer"` field from the JSON response. Honors a 120s timeout — openclaw subprocess calls typically complete in 15-30s, 120s gives generous headroom for tool-augmented runs.

No retry logic in the client itself. Caller decides retry behavior; for the daily digest, one failed attempt is acceptable — next scheduled run is in 3-4 days. Failures log a structured warning to `~/logs/health-digest.log` and exit non-zero so systemd records the failure.

### `_health/glossary.py` (new)

A single module-level dict:

```python
METRIC_GLOSSARY: dict[str, dict[str, str]] = {
    "resting_heart_rate": {
        "label": "Resting heart rate (RHR)",
        "units": "bpm",
        "definition": "Lowest heart rate observed during rest; tracked by the watch.",
        "healthy_range": "55-75 bpm for adults; lower trends with cardio fitness.",
        "concerning": "Sustained increase of 8-10 bpm from baseline often precedes illness; sustained > 100 bpm at rest is clinically abnormal.",
    },
    "heart_rate_variability": {
        "label": "Heart rate variability (HRV)",
        "units": "ms",
        "definition": "Beat-to-beat variation. Higher = better autonomic recovery.",
        "healthy_range": "Personalized; your typical range is the relevant reference.",
        "concerning": "A 20-30% drop sustained over 3+ days signals stress, illness, alcohol, or poor sleep.",
    },
    # ... one entry per metric in METRIC_REGISTRY
}

def format_for_prompt(findings: dict) -> str:
    """Render a Markdown glossary block covering only the metrics present in
    findings (skips _-prefixed and metrics with no glossary entry). Returned
    string is embedded in the LLM prompt."""
```

Entries to include (one per metric in `_health/metric_fetch.py:METRIC_REGISTRY`): RHR, HRV, sleep_duration, sleep_efficiency, sleep_heart_rate, blood_oxygen_saturation (overnight low), respiratory_rate, walking_heart_rate_average, walking_speed, walking_asymmetry_percentage, vo2_max, active_energy, exercise_time, step_count, flights_climbed, basal_body_temperature, apple_walking_steadiness, mindful_session, apple_stand_time, plus the three event metrics (irregular/high/low heart rate event).

Per-entry fields are kept short so injection doesn't bloat the prompt: roughly one sentence each for `definition`, `healthy_range`, `concerning`.

### `agents/health_digest.py` (new entry point)

```python
def main() -> int:
    today_weekday = datetime.now(timezone.utc).astimezone(LA_TZ).weekday()
    if today_weekday not in (1, 6):
        # Defensive — timer should already enforce this
        return 0
    prior_30d = _load_prior_jsonl(limit_lines=30)
    todays_findings = prior_30d[-1] if prior_30d else {}
    message = weekly_digest.run(
        prior_30d_jsonl=prior_30d,
        todays_findings=todays_findings,
        today_weekday=today_weekday,
    )
    if not message:
        return 0
    ok = telegram_notify.send(message + "\n\n" + DISCLAIMER)
    return 0 if ok else 1
```

`weekly_digest.run()` no longer takes an `llm_client` argument — it pulls the client internally from `jarvis_api_client`. This decouples the digest function from any specific LLM SDK.

### `agents/health_monitor.py` (modified)

Three edits:

1. Remove the `if today_weekday == retrospective.SUNDAY:` block (lines ~390-402) — that responsibility moves to `health_digest.py`.
2. Remove the import of `retrospective` from this file.
3. Keep `_filter_emergency_for_alert`, `_send_emergency_email`, `_log_findings`, `_enrich_findings_for_patterns`, `ask_llm_for_message`, `analyse` — all still in active use by the daily heartbeat.

The daily run continues to write JSONL, run patterns, anti-spam, brain-write, and emergency edge-trigger. If a non-emergency rule pattern fires, it still gets rendered + delivered via Telegram as today.

### `agents/emergency_check.py` — delete

This module exists only to support the 3h timer. With the timer collapsed into the 06:30 daily run, the module has no caller. Delete cleanly. Its emergency-detection logic was always sourced from `health_monitor.analyse()` + `_filter_emergency_for_alert()` — those stay.

### Systemd units

**`deploy/neb/systemd/health-monitor.timer`** — replace contents:

```ini
[Unit]
Description=Daily Jarvis health watchdog — Apple Health analysis + emergency edge-trigger
Requires=health-monitor.service

[Timer]
OnCalendar=*-*-* 11:00:00 America/Los_Angeles
Persistent=true

[Install]
WantedBy=timers.target
```

**`deploy/neb/systemd/health-digest.timer`** (new):

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

**`deploy/neb/systemd/health-digest.service`** (new):

```ini
[Unit]
Description=Jarvis health digest — narrative briefing via Jarvis /chat
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

`Requires=jarvis-api.service` ensures the API is up before the digest tries to call it. If the API is down, the digest service exits with a failure that systemd records.

**`deploy/neb/systemd/health-emergency-check.{service,timer}`** — delete.

## LLM prompt structure (digest path)

The existing v3.1 `retrospective.py` prompt is reused with three additions:

1. Top-level headline line varies by day (`*Midweek check-in, sir.*` vs `*Sunday briefing, sir.*`).
2. Framing line varies by day (see "Tue vs Sun framing" above).
3. A new `Metric glossary (for citations below):` block appears between the existing rollup tables and the existing journal block. Instruction added inline: *"When you cite a metric value in the briefing, also briefly state what it means and what a healthy range looks like, drawing on the glossary."*

All other prompt rules carry over verbatim: structured sections (Last week / What to watch / Inner state / Focus this week), journal-citation requirements, no hedging/disclaimers, "treat him as an adult reading his own mirror" tone.

## Sickness detection (new)

Early-warning pattern for cold/illness onset, surfaced via the same emergency notification mechanism as the doctor-contact tier but with rest-focused framing instead of clinical-urgency framing.

### Detector

New pattern `sickness_signal` in `_health/patterns.py`. Fires when **2+ of these 6 signals** are present (broader sensitivity than `possible_illness`'s 3-of-3 conjunction):

| Signal | Trigger | Source metric |
|---|---|---|
| RHR elevation | z ≥ +1.0 | `resting_heart_rate` |
| HRV depression | z ≤ −0.8 | `heart_rate_variability` |
| Wrist temperature elevation | z ≥ +1.0 | `basal_body_temperature` (Apple Vitals' strongest illness predictor) |
| Respiratory rate elevation | z ≥ +1.0 | `respiratory_rate` |
| Walking HR elevation | z ≥ +1.0 | `walking_heart_rate_average` |
| SpO2 depression | 2+ of last 3 nights z ≤ −0.8 OR `recent_mean` < 94% | `blood_oxygen_saturation` |

Why 2-of-6 instead of 3-of-3: pre-symptom illness onset (1–3 days before symptoms emerge) typically shows 2–3 vital drifts simultaneously, but not the full clinical signature. The existing `possible_illness` (high severity, 3-of-3) stays as the firmer "you're already sick" pattern. `sickness_signal` is the earlier heads-up.

The detector tolerates stale metrics: it checks each signal independently, only counts fresh ones, and requires at least 2 fresh-and-triggered to fire. SpO2 uses dual criteria (sustained z-dip OR absolute floor breach below 94%) because clinical SpO2 cares about the actual saturation level, not just variance.

### New severity tier

`SEVERITY_RANK` extends to:

```python
SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2, "sickness": 3, "emergency": 4}
```

Sickness ranks above high (warrants user attention NOW, not just in the next digest) but below emergency (rest + monitor, not seek a doctor).

### State eligibility

`sickness_signal` is eligible on `watch_on` AND `partial` (graceful degradation — fires on whatever subset of the 6 signals are fresh). On `watch_off` the detector returns None (no fresh signals to evaluate). Not subject to anti-spam 3-day cooldown — uses its own edge-trigger state file (parallel to emergency).

### Routing

Sickness patterns route through the same notification pipeline as emergency-tier:
- Edge-triggered via a NEW state file `~/jarvis/state/sickness.json` (mirrors the existing `emergency.json` structure). Persistent sickness patterns alert once on transition from inactive → active, stay silent on subsequent runs, re-fire when the pattern clears and re-activates.
- Telegram delivery via the same daily 11:00 PT watchdog
- Email mirror to `<your-configured-email>` (subject: `[Jarvis] Early illness signal detected: <headline>` — distinct from emergency subject)

### LLM framing (sickness-specific branch in `ask_llm_for_message`)

```
🤧 *Early illness signal, sir.*
*<pattern headline>*

<2-3 specific numbers that triggered it>

_Rest, hydrate, sleep extra. Ease back on training intensity._
_Watch for fever, sore throat, congestion, or fatigue over the next 24-48h._
_Pattern-based heads-up, not a diagnosis. Real-time emergencies are handled by the Watch itself._
```

Distinct from emergency-tier "If symptomatic now: seek emergency care / if asymptomatic: schedule clinical evaluation in 48h." Sickness framing is recovery-focused; emergency framing is care-seeking.

When BOTH sickness AND emergency patterns fire in the same run, the emergency framing takes the lead (emergency is more urgent) and the sickness signal is included as a secondary line — but the emergency state file and sickness state file dedupe independently.

## Urgent alert (existing, kept as-is)

The daily 11:00 PT watchdog continues to fire emergency-tier Telegram + email when *new* emergency patterns transition from inactive to active. Existing wording:

```
🚨 *EMERGENCY-TIER PATTERN, sir.*
*<pattern headline>*

<2-3 specific numbers that triggered it>

_If symptomatic now: seek emergency care, sir._
_If asymptomatic: schedule clinical evaluation within 48 hours._
_Real-time emergencies are handled by the Watch itself; this is a retrospective pattern flag on the past 24 hours of data._
```

Mirrored to `<your-configured-email>` via the existing `email_notify.send()` path.

No changes to the emergency framing or detection logic in this spec.

## Error handling

| Failure mode | Behavior |
|---|---|
| Jarvis API returns non-200 | `jarvis_api_client.chat()` raises; `health_digest.main()` logs the error, sends a fallback Telegram message ("Digest skipped — Jarvis API returned <status>. Recheck at next scheduled fire."), returns exit 1. |
| Jarvis API timeout (>120s) | Same as above with the message naming "timeout". |
| Empty JSONL log | `health_digest.main()` sends a short Telegram noting "no health data in window — taking the day off". Exit 0. |
| Missing JARVIS_API_TOKEN | Exit 2, no message sent (config error, systemd surfaces). |
| Telegram send fails | Exit 1, error logged. JSONL is not the right place to log digest delivery failures — that lives in `~/logs/health-digest.log`. |

The daily watchdog's error handling is unchanged from today.

## Tests

| Module | Test cases |
|---|---|
| `_health/jarvis_api_client.py` | Mock httpx: 200 returns the answer; non-200 raises with status in message; timeout raises; missing token raises clearly. |
| `_health/weekly_digest.py` | Day gate accepts Tue + Sun only; rejects other days. Tue headline + framing differs from Sun. Glossary block present in prompt when findings have metrics with glossary entries. Sparse journal (<2 entries) triggers fallback wording. |
| `_health/glossary.py` | `format_for_prompt(findings)` includes only metrics that exist in both findings AND the glossary; ignores `_`-prefixed keys; output is non-empty when at least one match exists. |
| `agents/health_digest.py` | Mock weekly_digest + telegram_notify: success path delivers message + disclaimer; empty JSONL path sends short notice; API failure path sends fallback notice and exits 1. |
| `agents/health_monitor.py` | Existing tests, plus: remove the retrospective integration test that's now obsolete; add a check that the Sunday code path no longer invokes retrospective from this module. |

Mock the Jarvis API and Telegram at the HTTP boundary — no real network calls.

## Deployment / migration

Order of operations on neb-server (after merge):

1. `cd ~/jarvis && git pull`
2. `uv sync` (no new deps expected; httpx is already pulled in)
3. `systemctl --user daemon-reload`
4. `systemctl --user stop health-emergency-check.timer health-emergency-check.service`
5. `systemctl --user disable health-emergency-check.timer health-emergency-check.service`
6. `rm ~/.config/systemd/user/health-emergency-check.{service,timer}` (or `systemctl --user revert` then delete)
7. `cp deploy/neb/systemd/health-monitor.timer ~/.config/systemd/user/`
8. `cp deploy/neb/systemd/health-digest.{service,timer} ~/.config/systemd/user/`
9. `systemctl --user daemon-reload`
10. `systemctl --user enable --now health-digest.timer`
11. `systemctl --user restart health-monitor.timer`
12. Verify: `systemctl --user list-timers --all | grep health` shows both timers with correct next-fire times in PT.

A manual smoke test before the next scheduled fire: `systemctl --user start health-digest.service` and check the Telegram delivery + `~/logs/health-digest.log`.

## Success criteria

- Tuesday 12:00 PT and Sunday 16:00 PT each deliver a Telegram digest with structured sections (Last week / What to watch / Inner state / Focus this week), citing both health numbers and journal phrases.
- Digest text references at least one metric with its definition / healthy range from the glossary, naturally woven in.
- Daily 11:00 PT watchdog continues to silently update JSONL + brain; emergency edge-trigger still fires Telegram + email when a new emergency-tier pattern appears.
- Watch-off / post-resume rebuild gating still works — verified by inspecting `_data_state` blocks in JSONL across a watch-off → watch-on cycle.
- No active references to `emergency_check.py` or `health-emergency-check.{timer,service}` remain. Old systemd units are gone from `~/.config/systemd/user/`.
- All new tests pass under `uv run pytest tests/jarvis/agents/`.

## Open items (deliberately deferred)

- **LLM swap for the rule-pattern alert renderer** (`ask_llm_for_message`). Currently gpt-4o-mini via direct OpenAI. Could be routed through Jarvis `/chat` for consistency, but the messages are short, the path is hot during emergencies, and the integration cost outweighs the consistency win right now. Revisit if/when a v3.3 cleanup pass touches this code.
- **Wrist temperature pattern.** v2 spec already noted: when wrist temperature begins exporting from HAE, add a `wrist_temp_anomaly` deviation pattern. Out of scope here.
- **Symptom keyword detection in journal entries.** Could trigger the urgent alert path if a journal entry mentions chest pain / dizziness / syncope. Deferred until journal cadence is closer to real-time (currently weekly Sunday export).
