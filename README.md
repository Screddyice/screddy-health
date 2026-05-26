# jarvis-health

Apple Health digest engine — twice-weekly narrative briefings, early-warning sickness detection, and edge-triggered emergency alerts. Extracted from a personal Jarvis system, generalized for anyone running [Health Auto Export](https://www.healthyapps.dev/health-auto-export) + a Telegram bot.

## What it does

You set up the [Health Auto Export](https://www.healthyapps.dev/health-auto-export) iPhone app to push Apple Health data to a server you control. This project then:

1. **Daily watchdog** — Pulls the last 30 days of health metrics, computes z-scores against personal baselines, runs 17 rule-based pattern detectors (low recovery, deconditioning, possible illness, overtraining, sleep debt, respiratory anomaly, cardio decline, gait anomaly, sickness signal, three emergency-tier patterns, and more), writes a JSONL audit log, and edge-triggers Telegram + email alerts only when a *new* sickness- or emergency-tier pattern transitions from inactive → active.

2. **Twice-weekly digest** — Tuesdays at noon and Sundays at 4pm (configurable), assembles a structured prompt with last-7-days metric rollup, glossary, and journal entries, posts it to an LLM endpoint of your choosing, and delivers a narrative briefing to Telegram with sections: Last week / What to watch / Inner state / Focus this week.

3. **Watch-off fallback** — When the Apple Watch is off the body, the digest still produces a useful read using phone-only metrics (steps, walking speed, walking asymmetry, stair flights). Wrist-derived metrics are explicitly listed as unavailable so the LLM doesn't hallucinate around them.

4. **Sickness early-warning (`sickness_signal`)** — Fires when 2+ of 6 vital signals align: resting HR up, HRV down, wrist temperature up, respiratory rate up, walking HR up, or SpO2 down (sustained dip or below 94%). Catches illness onset 1–3 days before symptoms emerge. Sits between `high` and `emergency` in the severity hierarchy.

5. **Emergency-tier patterns** — Cardiac event detection, severe respiratory, systemic inflammation, extreme heart rate. Fires Telegram + email with explicit care-seeking guidance: `If symptomatic now: seek emergency care. If asymptomatic: schedule clinical evaluation within 48 hours.`

## Architecture

```
                    ┌────────────────────────────────────┐
   Apple Watch      │  Health Auto Export iPhone app     │
   + iPhone   ───>  │  pushes to your HAE server         │
   sensors          │  (Node.js + MongoDB + Cloudflare   │
                    │   tunnel; see services/)           │
                    └─────────────────┬──────────────────┘
                                      │  HTTPS pull (with token)
                                      ▼
                    ┌────────────────────────────────────┐
                    │  jarvis.agents.health_monitor      │  daily timer
                    │  ─ pulls last 30d per metric       │
                    │  ─ z-scores against baseline       │
                    │  ─ runs 17 pattern detectors       │
                    │  ─ writes JSONL audit log          │
                    │  ─ edge-triggers tier alerts       │
                    └─────────────────┬──────────────────┘
                                      │
                          ┌───────────┴────────────┐
                          ▼                        ▼
              ┌──────────────────┐       ┌──────────────────┐
              │ Telegram (always)│       │ Email (sickness  │
              │ via Bot API      │       │  + emergency)    │
              │                  │       │ via Composio Gmail│
              └──────────────────┘       └──────────────────┘

                    ┌────────────────────────────────────┐
                    │  jarvis.agents.health_digest       │  Tue 12:00 +
                    │  ─ reads last 30d JSONL            │   Sun 16:00
                    │  ─ pulls journal entries (opt'l)   │   timers
                    │  ─ assembles prompt + glossary     │
                    │  ─ POSTs to your LLM endpoint      │
                    │  ─ delivers narrative to Telegram  │
                    └────────────────────────────────────┘
```

## Quick start

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- The [Health Auto Export](https://www.healthyapps.dev/health-auto-export) iPhone app, plus their [server companion](https://github.com/HealthyApps/health-auto-export-server) running somewhere you control. The companion exposes a REST API your install will pull from. See `services/health-auto-export/README.md` for the setup walkthrough.
- A Telegram bot ([create one with @BotFather](https://core.telegram.org/bots#how-do-i-create-a-bot)) and the chat ID you want digests delivered to.
- An LLM endpoint that accepts `POST {url}/chat` with `{"message": "..."}` and returns `{"answer": "..."}` JSON. The included client (`jarvis_api_client.py`) targets `http://127.0.0.1:8200/chat` by default — point it at OpenAI, Claude, a local llama.cpp, an openclaw agent, or any compatible wrapper.

### Install

```bash
git clone https://github.com/Screddyice/jarvis-health
cd jarvis-health
uv sync
```

### Configure

Create `.env` at the repo root (see `.env.example` for the full template). Minimum required:

```bash
# Apple Health Auto Export — point at your HAE companion server
APPLE_HEALTH_REMOTE_BASE_URL=https://your-hae-server.example.com
APPLE_HEALTH_REMOTE_READ_TOKEN=sk-your-hae-token

# Telegram
TELEGRAM_BOT_TOKEN=123456:ABCdefGHI
TELEGRAM_CHAT_ID=987654321

# LLM endpoint for digest generation
JARVIS_API_URL=http://127.0.0.1:8200/chat
JARVIS_API_TOKEN=your-bearer-token

# Optional — email mirror for sickness + emergency alerts
JARVIS_HEALTH_EMAIL_TO=you@example.com

# Optional — Postgres for durable journal + metrics history
DATABASE_URL=postgresql://user:pass@host/db

# Optional — override paths and timezone
JARVIS_HEALTH_JSONL_LOG_PATH=~/logs/health-monitor.jsonl
JARVIS_HEALTH_STATE_DIR=~/jarvis-health/state
JARVIS_HEALTH_TZ=America/Los_Angeles
```

### Run it manually

```bash
# Daily watchdog: analyse, write JSONL, edge-trigger alerts
uv run python -m jarvis.agents.health_monitor

# Twice-weekly digest: read JSONL, assemble prompt, deliver narrative
uv run python -m jarvis.agents.health_digest
```

### Schedule via systemd

Templates live in `deploy/systemd/`. They use `%h` substitution so they work for any user as long as the repo is at `~/jarvis-health/`:

```bash
cp deploy/systemd/*.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now health-monitor.timer health-digest.timer
systemctl --user list-timers --all | grep health
```

Adjust the `OnCalendar=` lines in the timer files if you want different fire times.

## Pattern detectors

| Severity | Pattern | Trigger |
|---|---|---|
| `emergency` | `emergency_cardiac_event` | Watch-detected AFib / high HR / low HR events |
| `emergency` | `emergency_severe_respiratory` | 2+ nights with SpO2 collapse OR respiratory rate ≥ 20 + z ≥ +2 |
| `emergency` | `emergency_systemic_inflammation` | Four-axis qSOFA-style signature (RHR + HRV + SpO2 + respiratory rate) |
| `emergency` | `emergency_extreme_heart_rate` | Sustained RHR ≥ 110 OR ≤ 45 (with normal baseline) |
| `sickness` | `sickness_signal` | 2+ of 6 vital signals aligned (early-warning, pre-symptomatic) |
| `high` | `possible_illness` | RHR↑ + HRV↓ + steps↓ all simultaneously past threshold |
| `high` | `respiratory_anomaly` | 3+ nights with SpO2 or respiratory rate anomaly |
| `moderate` | `low_recovery` | HRV z ≤ -1.0 AND RHR z ≥ +1.0 |
| `moderate` | `deconditioning` | Steps + energy + exercise all 14d trend ≤ -15% |
| `moderate` | `overtraining` | HRV suppressed while activity elevated |
| `moderate` | `sleep_debt` | 3+ nights below 6.5h OR 14d sleep avg drop ≥ 15% |
| `moderate` | `cumulative_strain` | 7d: sleep low + HRV suppressed + energy elevated |
| `moderate` | `recovery_score_drop` | Composite HRV + RHR + sleep dimension |
| `moderate` | `training_load_imbalance` | Acute:chronic ratio < 0.7 or > 1.3 |
| `moderate` | `circadian_drift` | Sleep timing variance ≥ 1.5h |
| `low` | `hrv_trend_down` | HRV 14d trend ≤ -15% |
| `low` | `cardio_fitness_decline` | VO2 max trend ≤ -5% over 90d |
| `low` | `gait_anomaly` | 3+ consecutive days walking asymmetry/speed anomaly |

Emergency- and sickness-tier patterns bypass the 3-day anti-spam cooldown that suppresses repeat lower-tier fires — they each maintain their own edge-trigger state file.

## Layout

```
src/jarvis/
├── agents/
│   ├── health_monitor.py      ← daily watchdog entry point
│   ├── health_digest.py       ← Tue/Sun digest entry point
│   ├── pipeline_watchdog.py   ← optional freshness checker
│   └── _health/
│       ├── patterns.py        ← 17-pattern rule detector
│       ├── metric_fetch.py    ← Apple Health Auto Export client
│       ├── data_state.py      ← watch on/off/partial classifier
│       ├── glossary.py        ← metric definitions for the LLM
│       ├── weekly_digest.py   ← prompt assembly + LLM dispatch
│       └── jarvis_api_client.py ← HTTP client for the chat endpoint
├── channels/
│   ├── telegram_notify.py     ← Telegram delivery
│   └── email_notify.py        ← Composio Gmail delivery (optional)
├── connectors/
│   └── apple_health_remote.py ← HAE server client
└── brain.py                   ← optional Postgres durable store

services/health-auto-export/   ← HAE companion server setup docs
deploy/systemd/                ← systemd unit templates
docs/                          ← full design spec + implementation plan
tests/                         ← pytest suite (124 tests)
```

## Configuration reference

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `APPLE_HEALTH_REMOTE_BASE_URL` | Yes | — | HAE companion server URL |
| `APPLE_HEALTH_REMOTE_READ_TOKEN` | Yes | — | HAE read API token |
| `TELEGRAM_BOT_TOKEN` | Yes | — | Telegram bot token (BotFather) |
| `TELEGRAM_CHAT_ID` | Yes | — | Numeric chat ID for digest delivery |
| `JARVIS_API_URL` | Yes | `http://127.0.0.1:8200/chat` | LLM endpoint for digest |
| `JARVIS_API_TOKEN` | Yes | — | Bearer token for the LLM endpoint |
| `JARVIS_HEALTH_EMAIL_TO` | No | unset | Email recipient for sickness + emergency mirrors |
| `JARVIS_HEALTH_EMERGENCY_EMAIL_TO` | No | falls back to `JARVIS_HEALTH_EMAIL_TO` | Override emergency-only recipient |
| `JARVIS_HEALTH_SICKNESS_EMAIL_TO` | No | falls back to `JARVIS_HEALTH_EMERGENCY_EMAIL_TO` | Override sickness-only recipient |
| `JARVIS_HEALTH_JSONL_LOG_PATH` | No | `~/logs/health-monitor.jsonl` | Where the audit log is written |
| `JARVIS_HEALTH_STATE_DIR` | No | `~/jarvis/state` | Where edge-trigger state files live |
| `JARVIS_HEALTH_TZ` | No | `America/Los_Angeles` | Timezone for digest weekday gating |
| `DATABASE_URL` | No | unset (graceful no-op) | Postgres for durable history + journal reads |

## Design docs

The full design spec and implementation plan are in `docs/`:

- [`docs/health-digest-design.md`](docs/health-digest-design.md) — the engineering spec covering schedule, pattern detectors, LLM prompt structure, urgent alert framing, sickness detection, watch-state handling.
- [`docs/health-digest-plan.md`](docs/health-digest-plan.md) — the task-by-task implementation plan that was executed by Claude Code's [Superpowers](https://github.com/garrytan/gstack) workflow.

These were originally written for the author's private Jarvis system. They're preserved verbatim because the engineering process (design → plan → review → ship) is the actually interesting bit; the deployment-specific references are explained in provenance banners.

## Tests

```bash
uv run pytest tests/ -v
```

124 tests cover: pattern detector behavior for every severity tier, edge-trigger dedup state machines, prompt assembly with glossary injection, watch-off fallback path, journal sparse-data handling, HTTP client error paths, anti-spam cooldown, and end-to-end orchestrator flow.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [Health Auto Export](https://www.healthyapps.dev/health-auto-export) for making Apple Health data programmatically accessible.
- [WHOOP day-to-day variability research](https://www.whoop.com/) (Olympic athletes cohort) for baseline noise floor calibration.
- Apple's [Vitals feature](https://support.apple.com/guide/iphone/get-started-with-vitals-iph09e0bdec3/ios) for the multi-metric simultaneous-deviation pattern that several detectors use as a reference.
