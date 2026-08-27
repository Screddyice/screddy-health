"""Rule-based pattern detector. Each pattern is a pure function:
takes a findings dict, returns either None or a dict describing the fired pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


SEVERITY_RANK = {"low": 0, "moderate": 1, "high": 2, "sickness": 3, "emergency": 4}


# Sickness detector thresholds — calibrated for early-warning sensitivity
# (1-3 days before symptom onset). See spec design doc for clinical basis.
SICKNESS_Z_ELEVATED = 1.0          # RHR, wrist temp, resp rate, walking HR upper threshold
SICKNESS_Z_HRV_DOWN = -0.8         # HRV depression threshold (negative)
SICKNESS_Z_SPO2_DIP = -0.8         # SpO2 per-day z-score for sustained-dip counting
SICKNESS_SPO2_FLOOR_PCT = 94.0     # SpO2 absolute floor (% saturation)
SICKNESS_SPO2_SUSTAINED_DAYS = 2   # min recent-3d days at z<=-0.8 to count as sustained dip
SICKNESS_MIN_SIGNALS = 2           # min signals firing to declare sickness_signal


@dataclass(frozen=True)
class PatternDef:
    id: str
    severity: str
    requires_metrics: tuple[str, ...]
    detector: Callable[[dict], dict | None]


def _z(findings: dict, metric: str) -> float:
    return (findings.get(metric) or {}).get("z_score", 0.0) or 0.0


def _pct(findings: dict, metric: str) -> float:
    return (findings.get(metric) or {}).get("trend_pct_change_14d", 0.0) or 0.0


def _recent_mean(findings: dict, metric: str) -> float:
    return (findings.get(metric) or {}).get("recent_mean", 0.0) or 0.0


# === Pattern detectors (filled in by Tasks 9–13) ===


def detect_low_recovery(findings: dict) -> dict | None:
    if _z(findings, "heart_rate_variability") <= -1.0 and _z(findings, "resting_heart_rate") >= 1.0:
        return {
            "id": "low_recovery",
            "severity": "moderate",
            "headline": "Recovery markers worsening",
            "evidence": {
                "hrv_z": _z(findings, "heart_rate_variability"),
                "hrv_recent_mean": _recent_mean(findings, "heart_rate_variability"),
                "rhr_z": _z(findings, "resting_heart_rate"),
                "rhr_recent_mean": _recent_mean(findings, "resting_heart_rate"),
            },
            "interpretation": (
                "HRV is below baseline AND resting heart rate is above baseline. "
                "Common drivers: poor sleep, accumulated stress, illness onset, "
                "alcohol, dehydration, or overtraining."
            ),
        }
    return None


def detect_deconditioning(findings: dict) -> dict | None:
    pcts = [
        _pct(findings, "step_count"),
        _pct(findings, "active_energy"),
        _pct(findings, "apple_exercise_time"),
    ]
    if all(p <= -0.15 for p in pcts):
        return {
            "id": "deconditioning",
            "severity": "moderate",
            "headline": "Activity has dropped across the board",
            "evidence": {
                "step_count_pct_14d": pcts[0],
                "active_energy_pct_14d": pcts[1],
                "exercise_time_pct_14d": pcts[2],
            },
            "interpretation": (
                "Steps, active energy, and exercise time all trending down 15%+ "
                "over 14 days. Could be travel/schedule, injury, illness, or burnout. "
                "If sustained 3+ weeks, expect measurable cardio fitness loss."
            ),
        }
    return None


def detect_possible_illness(findings: dict) -> dict | None:
    if (
        _z(findings, "resting_heart_rate") >= 1.5
        and _z(findings, "heart_rate_variability") <= -1.0
        and _z(findings, "step_count") <= -1.0
    ):
        return {
            "id": "possible_illness",
            "severity": "high",
            "headline": "Pattern consistent with illness onset",
            "evidence": {
                "rhr_z": _z(findings, "resting_heart_rate"),
                "hrv_z": _z(findings, "heart_rate_variability"),
                "steps_z": _z(findings, "step_count"),
            },
            "interpretation": (
                "Elevated resting HR, suppressed HRV, and reduced movement in concert "
                "often appear with viral illness, infection, or significant inflammation. "
                "Hydrate, rest, monitor temp."
            ),
        }
    return None


def detect_overtraining(findings: dict) -> dict | None:
    if _z(findings, "heart_rate_variability") <= -1.0 and _z(findings, "active_energy") >= 1.0:
        return {
            "id": "overtraining",
            "severity": "moderate",
            "headline": "Possible overtraining",
            "evidence": {
                "hrv_z": _z(findings, "heart_rate_variability"),
                "energy_z": _z(findings, "active_energy"),
            },
            "interpretation": (
                "HRV is suppressed while activity is elevated — autonomic system isn't "
                "recovering at the pace you're loading it. Consider a deload day or extra sleep."
            ),
        }
    return None


def detect_hrv_trend_down(findings: dict) -> dict | None:
    # v2 threshold: -15% (was -10%; bumped above WHOOP's 5.5% daily noise floor)
    if _pct(findings, "heart_rate_variability") <= -0.15:
        return {
            "id": "hrv_trend_down",
            "severity": "low",
            "headline": "HRV trending down over 14 days",
            "evidence": {
                "hrv_pct_14d": _pct(findings, "heart_rate_variability"),
                "hrv_recent_mean": _recent_mean(findings, "heart_rate_variability"),
            },
            "interpretation": (
                "HRV gradually decreasing without an acute spike elsewhere. "
                "Often reflects accumulating stress or poor sleep quality. "
                "Worth correlating with sleep + workload."
            ),
        }
    return None


def detect_sleep_debt(findings: dict) -> dict | None:
    sleep = findings.get("sleep_analysis", {}) or {}
    recent_3d = sleep.get("recent_3d_values", []) or []
    short_nights = sum(1 for h in recent_3d if h < 6.5)
    pct_14d = sleep.get("trend_pct_change_14d", 0.0) or 0.0

    if short_nights >= 3 or pct_14d <= -0.15:
        return {
            "id": "sleep_debt",
            "severity": "moderate",
            "headline": "Sleep debt accumulating",
            "evidence": {
                "recent_3d_values_hours": recent_3d,
                "short_nights_count": short_nights,
                "trend_14d_pct": pct_14d,
            },
            "interpretation": (
                "Three or more nights below 6.5h, or a 14-day average decline of "
                "15%+. Sustained sleep debt drives HRV down and RHR up before "
                "those metrics show their own anomalies. Aim for one full-recovery "
                "night before evaluating downstream signals."
            ),
        }
    return None


def detect_respiratory_anomaly(findings: dict) -> dict | None:
    spo2 = findings.get("blood_oxygen_saturation", {}) or {}
    resp = findings.get("respiratory_rate", {}) or {}
    spo2_zs = spo2.get("recent_3d_z_scores", []) or []
    resp_zs = resp.get("recent_3d_z_scores", []) or []

    spo2_anomalous = len(spo2_zs) >= 3 and all(z <= -1.5 for z in spo2_zs[-3:])
    resp_anomalous = len(resp_zs) >= 3 and all(z >= 1.5 for z in resp_zs[-3:])

    if spo2_anomalous or resp_anomalous:
        return {
            "id": "respiratory_anomaly",
            "severity": "high",
            "headline": "Respiratory pattern anomaly across 3 nights",
            "evidence": {
                "spo2_recent_3d_z": spo2_zs,
                "respiratory_rate_recent_3d_z": resp_zs,
            },
            "interpretation": (
                "Three consecutive nights of low overnight SpO2 (z ≤ −1.5) or "
                "elevated respiratory rate (z ≥ +1.5). Common drivers: respiratory "
                "illness, altitude, sleep apnea, or significant inflammation."
            ),
        }
    return None


def detect_cardio_fitness_decline(findings: dict) -> dict | None:
    vo2 = findings.get("vo2_max", {}) or {}
    pct_90d = vo2.get("trend_pct_change_90d", 0.0) or 0.0
    if pct_90d <= -0.05:
        return {
            "id": "cardio_fitness_decline",
            "severity": "low",
            "headline": "Cardio fitness trending down over 90 days",
            "evidence": {
                "vo2_max_pct_90d": pct_90d,
                "vo2_max_recent": vo2.get("recent_mean"),
            },
            "interpretation": (
                "VO2 max has dropped 5%+ over the last 90 days (about 13 weekly "
                "readings). Natural age-related decline is ~1-2%/year, so this "
                "is faster than expected. Likely tied to reduced training load."
            ),
        }
    return None


def detect_gait_anomaly(findings: dict) -> dict | None:
    asym = findings.get("walking_asymmetry_percentage", {}) or {}
    speed = findings.get("walking_speed", {}) or {}
    asym_zs = asym.get("recent_3d_z_scores", []) or []
    speed_zs = speed.get("recent_3d_z_scores", []) or []

    asym_anomalous = len(asym_zs) >= 3 and all(z >= 2.0 for z in asym_zs[-3:])
    speed_anomalous = len(speed_zs) >= 3 and all(z <= -2.0 for z in speed_zs[-3:])

    if asym_anomalous or speed_anomalous:
        return {
            "id": "gait_anomaly",
            "severity": "low",
            "headline": "Gait pattern anomaly across 3 days",
            "evidence": {
                "asymmetry_recent_3d_z": asym_zs,
                "walking_speed_recent_3d_z": speed_zs,
            },
            "interpretation": (
                "Three consecutive days of significantly elevated walking asymmetry "
                "or reduced walking speed. Could indicate orthopedic issue, fatigue, "
                "or compensating for an injury. Worth tracking if it persists."
            ),
        }
    return None


def detect_cumulative_strain(findings: dict) -> dict | None:
    sleep = findings.get("sleep_analysis", {}) or {}
    hrv = findings.get("heart_rate_variability", {}) or {}
    energy = findings.get("active_energy", {}) or {}

    sleep_avg = sleep.get("trend_7d_avg_hours", 99.0)
    hrv_z = hrv.get("z_score", 0.0) or 0.0
    energy_z = energy.get("z_score", 0.0) or 0.0

    if sleep_avg < 6.5 and hrv_z <= -1.0 and energy_z >= 0.5:
        return {
            "id": "cumulative_strain",
            "severity": "moderate",
            "headline": "Cumulative strain across 7 days",
            "evidence": {
                "sleep_avg_7d_hours": sleep_avg,
                "hrv_z": hrv_z,
                "active_energy_z": energy_z,
            },
            "interpretation": (
                "Compounding signals: sleep averaging <6.5h, HRV suppressed, and "
                "active energy elevated — sustained for a week. The combination "
                "predicts performance decline if not unloaded."
            ),
        }
    return None


def detect_recovery_score_drop(findings: dict) -> dict | None:
    """Composite recovery score across HRV (higher=better), RHR (lower=better),
    and recent sleep duration. Each axis maps to roughly [-2, +2]; the mean
    fires moderate at <= -1.0 SD and high at <= -1.5 SD.

    Distinct from low_recovery (HRV+RHR only) because it folds in sleep — the
    most actionable lever — and gives a quantitative score Screddy can
    reference in retros.
    """
    hrv_z = _z(findings, "heart_rate_variability")
    rhr_z = _z(findings, "resting_heart_rate")
    sleep = findings.get("sleep_analysis", {}) or {}
    sleep_recent_avg = sleep.get("recent_mean", 0.0) or 0.0

    hrv_axis = max(-2.0, min(2.0, hrv_z))
    rhr_axis = max(-2.0, min(2.0, -rhr_z))
    sleep_axis = max(-2.0, min(2.0, sleep_recent_avg - 7.0))
    composite = (hrv_axis + rhr_axis + sleep_axis) / 3.0

    if composite > -1.0:
        return None
    return {
        "id": "recovery_score_drop",
        "severity": "high" if composite <= -1.5 else "moderate",
        "headline": f"Composite recovery score at {composite:.2f}",
        "evidence": {
            "composite_score": composite,
            "hrv_z": hrv_z,
            "rhr_z": rhr_z,
            "sleep_recent_avg_hours": sleep_recent_avg,
        },
        "interpretation": (
            "HRV, resting heart rate, and recent sleep are compounding into a sub-baseline "
            "recovery state. Treat as a forcing function: prioritize 8+ hours tonight, "
            "reduce training intensity for 1-2 days, hydrate, and recheck tomorrow."
        ),
    }


def detect_training_load_imbalance(findings: dict) -> dict | None:
    """Acute (7d) vs chronic (28d) exercise minutes — a Banister/ACWR-style ratio.

    Values above 1.5 correlate with elevated injury and burnout risk; below 0.8
    indicates detraining. The enrichment step in health_monitor populates the
    `acute_chronic_ratio` field.
    """
    ex = findings.get("apple_exercise_time", {}) or {}
    ratio = ex.get("acute_chronic_ratio")
    if ratio is None:
        return None
    acute = ex.get("acute_7d_total_minutes")
    chronic_weekly = ex.get("chronic_28d_avg_weekly_minutes")

    if ratio >= 1.5:
        return {
            "id": "training_load_overreach",
            "severity": "moderate",
            "headline": f"Training load ratio {ratio:.2f} (overreach zone)",
            "evidence": {
                "acute_7d_total_minutes": acute,
                "chronic_28d_avg_weekly_minutes": chronic_weekly,
                "acute_chronic_ratio": ratio,
            },
            "interpretation": (
                "The last 7 days carry 50%+ more exercise volume than your 4-week average. "
                "Ratios above 1.5 are when injury and HRV-suppression risk climbs sharply. "
                "Either schedule a deload week or hold this level long enough for the chronic "
                "average to catch up."
            ),
        }
    if ratio <= 0.8:
        return {
            "id": "training_load_detraining",
            "severity": "low",
            "headline": f"Training load ratio {ratio:.2f} (detraining zone)",
            "evidence": {
                "acute_7d_total_minutes": acute,
                "chronic_28d_avg_weekly_minutes": chronic_weekly,
                "acute_chronic_ratio": ratio,
            },
            "interpretation": (
                "The last 7 days are 20%+ below your 4-week average. Sustained for "
                "another 2 weeks, expect VO2max and HRV to follow. Worth a check on "
                "whether this is a planned taper or schedule slip."
            ),
        }
    return None


def detect_circadian_drift(findings: dict) -> dict | None:
    """Sleep-schedule chaos via 7-night standard deviation of sleep hours.
    A stdev >= 1.5h flags inconsistent timing, which correlates with mood,
    attention, and next-day HRV instability."""
    sleep = findings.get("sleep_analysis", {}) or {}
    stdev = sleep.get("stdev_7d_hours") or 0.0
    if stdev < 1.5:
        return None
    return {
        "id": "circadian_drift",
        "severity": "moderate",
        "headline": f"Sleep duration varies ±{stdev:.1f}h across 7 nights",
        "evidence": {
            "stdev_7d_hours": stdev,
            "recent_7d_values_hours": sleep.get("recent_7d_values", []),
        },
        "interpretation": (
            "Sleep duration's 7-night standard deviation has crossed 1.5 hours. "
            "Variable sleep timing is one of the strongest predictors of next-day "
            "HRV suppression and mood drift. Target ±30 minutes of lights-out "
            "consistency before optimizing duration."
        ),
    }


# === Sickness-tier detector ===
#
# Early-warning illness signal — sits between high and emergency in the
# severity ladder. Self-gates per-metric staleness so it can fire on a partial
# subset of vital signals when some metrics are stale. Routed through a
# separate edge-trigger state file (sickness.json), distinct from emergency.


def detect_sickness_signal(findings: dict) -> dict | None:
    """Early-warning sickness detector — fires when 2+ of 6 vital signals align.

    Self-gates per-metric staleness (signals from stale metrics don't count
    toward the 2-of-6 threshold). Distinct from `possible_illness`
    (3-of-3 confluence, severity high) — this is the heads-up tier,
    severity 'sickness' (between high and emergency).
    """
    signals: list[str] = []
    evidence: dict = {}

    def _fresh(metric: str) -> bool:
        return not (findings.get(metric) or {}).get("stale", True)

    # 1. RHR elevation
    if _fresh("resting_heart_rate"):
        rhr_z = _z(findings, "resting_heart_rate")
        if rhr_z >= SICKNESS_Z_ELEVATED:
            signals.append("rhr_up")
            evidence["rhr_z"] = rhr_z
            evidence["rhr_recent_mean"] = _recent_mean(findings, "resting_heart_rate")

    # 2. HRV depression
    if _fresh("heart_rate_variability"):
        hrv_z = _z(findings, "heart_rate_variability")
        if hrv_z <= SICKNESS_Z_HRV_DOWN:
            signals.append("hrv_down")
            evidence["hrv_z"] = hrv_z
            evidence["hrv_recent_mean"] = _recent_mean(findings, "heart_rate_variability")

    # 3. Wrist temperature elevation
    if _fresh("basal_body_temperature"):
        temp_z = _z(findings, "basal_body_temperature")
        if temp_z >= SICKNESS_Z_ELEVATED:
            signals.append("wrist_temp_up")
            evidence["wrist_temp_z"] = temp_z
            evidence["wrist_temp_recent_mean"] = _recent_mean(findings, "basal_body_temperature")

    # 4. Respiratory rate elevation
    if _fresh("respiratory_rate"):
        resp_z = _z(findings, "respiratory_rate")
        if resp_z >= SICKNESS_Z_ELEVATED:
            signals.append("resp_rate_up")
            evidence["resp_rate_z"] = resp_z
            evidence["resp_rate_recent_mean"] = _recent_mean(findings, "respiratory_rate")

    # 5. Walking heart rate elevation
    if _fresh("walking_heart_rate_average"):
        walk_z = _z(findings, "walking_heart_rate_average")
        if walk_z >= SICKNESS_Z_ELEVATED:
            signals.append("walking_hr_up")
            evidence["walking_hr_z"] = walk_z
            evidence["walking_hr_recent_mean"] = _recent_mean(findings, "walking_heart_rate_average")

    # 6. SpO2 depression — sustained z-dip OR absolute floor breach
    spo2 = findings.get("blood_oxygen_saturation") or {}
    if not spo2.get("stale", True):
        recent_mean = spo2.get("recent_mean", 100.0) or 100.0
        recent_3d_z = spo2.get("recent_3d_z_scores") or []
        sustained_dip = sum(
            1 for z in recent_3d_z if z is not None and z <= SICKNESS_Z_SPO2_DIP
        ) >= SICKNESS_SPO2_SUSTAINED_DAYS
        absolute_dip = recent_mean < SICKNESS_SPO2_FLOOR_PCT
        if sustained_dip or absolute_dip:
            signals.append("spo2_down")
            evidence["spo2_z"] = _z(findings, "blood_oxygen_saturation")
            evidence["spo2_recent_mean"] = recent_mean
            evidence["spo2_sustained_dip"] = sustained_dip
            evidence["spo2_absolute_dip"] = absolute_dip

    if len(signals) < SICKNESS_MIN_SIGNALS:
        return None

    return {
        "id": "sickness_signal",
        "severity": "sickness",
        "headline": f"Early illness signal — {len(signals)} of 6 markers concerning",
        "evidence": {
            "signals_fired": signals,
            **evidence,
        },
        "interpretation": (
            "Multiple vital markers are drifting in the direction that typically "
            "precedes illness onset by 1-3 days. Not a diagnosis — a heads-up. "
            "Rest, hydrate, prioritize sleep, ease training intensity. Monitor "
            "for fever, sore throat, congestion, or fatigue over the next 24-48h."
        ),
    }


# === Emergency-tier detectors ===
#
# Fire when the data shape suggests something clinically actionable in the
# recent window (last 24-72h). They bypass the 3-day anti-spam cooldown — a
# persisting acute pattern is signal, not noise. Phrasing carefully separates
# "real-time emergency" (which is the Watch's job) from "retrospective pattern
# warranting clinical attention" (which is this layer's job).


def detect_emergency_cardiac_event(findings: dict) -> dict | None:
    """Fires when the Watch flagged ANY cardiac rhythm/HR event in the last day.

    These metrics are clinical-grade Apple Watch alerts (AFib, high/low HR).
    The Watch only writes a record when its on-device algorithm decided the
    event was real, so even count=1 is meaningful. Relies on the enrichment
    step populating `latest_day_count` for each event metric.
    """
    fires: list[tuple[str, float]] = []
    metric_label = {
        "irregular_heart_rhythm_event": "irregular rhythm (AFib indication)",
        "high_heart_rate_event": "high heart rate event",
        "low_heart_rate_event": "low heart rate event",
    }
    for mid, label in metric_label.items():
        blob = findings.get(mid) or {}
        count = blob.get("latest_day_count", 0) or 0
        if count >= 1:
            fires.append((label, count))
    if not fires:
        return None
    return {
        "id": "emergency_cardiac_event",
        "severity": "emergency",
        "headline": "Apple Watch flagged cardiac event(s) in the last 24h",
        "evidence": {"events": [{"type": e, "count": c} for e, c in fires]},
        "interpretation": (
            "The Watch's on-device algorithm logged one or more cardiac rhythm or "
            "heart-rate events yesterday. Even isolated events are typically worth a "
            "clinical follow-up. If symptomatic now (chest pain, shortness of breath, "
            "syncope, palpitations), treat as emergency. If asymptomatic, schedule "
            "with a physician within 48 hours."
        ),
    }


def detect_emergency_severe_respiratory(findings: dict) -> dict | None:
    """Severe sustained respiratory anomaly across 2+ of the last 3 nights.

    SpO2: at least 2 of the last 3 nights with z <= -2.5 (severe drop). Stricter
    than respiratory_anomaly which fires at z <= -1.5 across all 3.
    Respiratory rate: recent_mean >= 20 AND z >= 2.0 (sustained elevation).
    """
    spo2 = findings.get("blood_oxygen_saturation", {}) or {}
    resp = findings.get("respiratory_rate", {}) or {}
    spo2_zs = spo2.get("recent_3d_z_scores", []) or []
    severe_nights = sum(1 for z in spo2_zs[-3:] if z <= -2.5)
    spo2_emergency = severe_nights >= 2

    resp_mean = resp.get("recent_mean", 0.0) or 0.0
    resp_z = resp.get("z_score", 0.0) or 0.0
    resp_emergency = resp_mean >= 20.0 and resp_z >= 2.0

    if not (spo2_emergency or resp_emergency):
        return None
    triggers: list[str] = []
    if spo2_emergency:
        triggers.append(f"SpO2 z<=−2.5 on {severe_nights} of last 3 nights")
    if resp_emergency:
        triggers.append(f"respiratory rate {resp_mean:.1f}/min sustained (z={resp_z:.1f})")
    return {
        "id": "emergency_severe_respiratory",
        "severity": "emergency",
        "headline": "Severe respiratory pattern",
        "evidence": {
            "spo2_recent_3d_z": spo2_zs,
            "spo2_severe_nights": severe_nights,
            "respiratory_rate_recent_mean": resp_mean,
            "respiratory_rate_z": resp_z,
            "triggers": triggers,
        },
        "interpretation": (
            "Overnight oxygen saturation has dropped well below baseline, or "
            "respiratory rate is running sustained-elevated. Possible drivers: "
            "pneumonia, severe sleep apnea, cardiopulmonary stress, viral illness "
            "with hypoxia. If symptomatic (shortness of breath, persistent cough, "
            "fever), seek urgent care. Otherwise schedule clinical evaluation this week."
        ),
    }


def detect_emergency_systemic_inflammation(findings: dict) -> dict | None:
    """Quad-signal sepsis/systemic-inflammation signature.

    Requires ALL FOUR conditions to fire simultaneously — keeps false-positive
    rate very low. Mirrors the spirit of hospital early-warning scores
    (qSOFA, MEWS) using the metrics HAE actually exports.
    """
    rhr_z = _z(findings, "resting_heart_rate")
    hrv_z = _z(findings, "heart_rate_variability")
    spo2 = findings.get("blood_oxygen_saturation", {}) or {}
    resp = findings.get("respiratory_rate", {}) or {}
    spo2_zs = spo2.get("recent_3d_z_scores", []) or []
    spo2_recent_min_z = min(spo2_zs[-3:]) if spo2_zs else 0.0
    resp_z = resp.get("z_score", 0.0) or 0.0

    if not (rhr_z >= 2.0 and hrv_z <= -2.0 and spo2_recent_min_z <= -1.5 and resp_z >= 1.5):
        return None
    return {
        "id": "emergency_systemic_inflammation",
        "severity": "emergency",
        "headline": "Composite signature consistent with systemic inflammation",
        "evidence": {
            "rhr_z": rhr_z,
            "hrv_z": hrv_z,
            "spo2_recent_min_z": spo2_recent_min_z,
            "respiratory_rate_z": resp_z,
        },
        "interpretation": (
            "Resting HR elevated, HRV suppressed, oxygen saturation dropped, and "
            "respiratory rate up — all simultaneously, all well past their baselines. "
            "This four-axis signature is what hospital early-warning scores (qSOFA, "
            "MEWS) use to flag sepsis or systemic infection risk. Treat as urgent: "
            "if symptomatic with fever, confusion, or severe fatigue, seek emergency "
            "care. If asymptomatic, contact your physician today."
        ),
    }


def detect_emergency_extreme_heart_rate(findings: dict) -> dict | None:
    """Sustained extreme resting heart rate (3-day recent average).

    High: >= 110 bpm sustained. Catches sinus tachycardia from infection,
    dehydration, thyroid abnormality, or arrhythmia not picked up by the
    Watch's discrete-event detector.
    Low: <= 45 bpm AND baseline was normal (>= 55) — catches a meaningful
    drop, not normal athlete bradycardia.
    """
    rhr = findings.get("resting_heart_rate", {}) or {}
    recent = rhr.get("recent_mean", 0.0) or 0.0
    baseline = rhr.get("baseline_mean", 0.0) or 0.0
    if recent >= 110.0:
        return {
            "id": "emergency_extreme_heart_rate",
            "severity": "emergency",
            "headline": f"Resting HR {recent:.0f} bpm sustained",
            "evidence": {
                "rhr_recent_mean": recent,
                "rhr_baseline_mean": baseline,
                "direction": "high",
            },
            "interpretation": (
                "Resting heart rate has held at 110+ bpm across the last 3 days. "
                "Drivers worth ruling out: dehydration, fever/infection, anemia, "
                "thyroid abnormality, arrhythmia, severe sleep deprivation, "
                "stimulant overuse. Schedule clinical evaluation within 24 hours, "
                "sooner if symptomatic."
            ),
        }
    if recent <= 45.0 and baseline >= 55.0:
        return {
            "id": "emergency_extreme_heart_rate",
            "severity": "emergency",
            "headline": f"Resting HR {recent:.0f} bpm — sharp drop from baseline {baseline:.0f}",
            "evidence": {
                "rhr_recent_mean": recent,
                "rhr_baseline_mean": baseline,
                "direction": "low",
            },
            "interpretation": (
                "Resting heart rate is at or below 45 bpm with a normal baseline "
                "in the mid-50s+. This is not athlete bradycardia — it's a real "
                "drop. Drivers worth ruling out: medication effect (beta-blocker "
                "dose change), electrolyte imbalance, heart block, hypothyroidism. "
                "Treat as urgent — schedule clinical evaluation today."
            ),
        }
    return None


PATTERN_REGISTRY: tuple[PatternDef, ...] = (
    PatternDef("low_recovery", "moderate",
               ("heart_rate_variability", "resting_heart_rate"), detect_low_recovery),
    PatternDef("deconditioning", "moderate",
               ("step_count", "active_energy", "apple_exercise_time"), detect_deconditioning),
    PatternDef("possible_illness", "high",
               ("resting_heart_rate", "heart_rate_variability", "step_count"), detect_possible_illness),
    PatternDef("overtraining", "moderate",
               ("heart_rate_variability", "active_energy"), detect_overtraining),
    PatternDef("hrv_trend_down", "low",
               ("heart_rate_variability",), detect_hrv_trend_down),
    PatternDef("sleep_debt", "moderate",
               ("sleep_analysis",), detect_sleep_debt),
    PatternDef("respiratory_anomaly", "high",
               ("blood_oxygen_saturation", "respiratory_rate"), detect_respiratory_anomaly),
    PatternDef("cardio_fitness_decline", "low",
               ("vo2_max",), detect_cardio_fitness_decline),
    PatternDef("gait_anomaly", "low",
               ("walking_asymmetry_percentage", "walking_speed"), detect_gait_anomaly),
    PatternDef("cumulative_strain", "moderate",
               ("sleep_analysis", "heart_rate_variability", "active_energy"), detect_cumulative_strain),
    PatternDef("recovery_score_drop", "moderate",
               ("heart_rate_variability", "resting_heart_rate", "sleep_analysis"),
               detect_recovery_score_drop),
    PatternDef("training_load_imbalance", "moderate",
               ("apple_exercise_time",), detect_training_load_imbalance),
    PatternDef("circadian_drift", "moderate",
               ("sleep_analysis",), detect_circadian_drift),
    # Sickness-tier — bypass anti-spam, self-gate per-metric staleness.
    # Empty requires_metrics: detector internally handles per-signal freshness
    # so it can fire on whatever subset of the 6 signals is fresh.
    PatternDef("sickness_signal", "sickness",
               (),  # detector self-gates per-metric staleness; no requires_metrics
               detect_sickness_signal),
    # Emergency-tier — bypass anti-spam, surface in distinct LLM framing
    PatternDef("emergency_cardiac_event", "emergency",
               ("irregular_heart_rhythm_event", "high_heart_rate_event", "low_heart_rate_event"),
               detect_emergency_cardiac_event),
    PatternDef("emergency_severe_respiratory", "emergency",
               ("blood_oxygen_saturation", "respiratory_rate"),
               detect_emergency_severe_respiratory),
    PatternDef("emergency_systemic_inflammation", "emergency",
               ("resting_heart_rate", "heart_rate_variability",
                "blood_oxygen_saturation", "respiratory_rate"),
               detect_emergency_systemic_inflammation),
    PatternDef("emergency_extreme_heart_rate", "emergency",
               ("resting_heart_rate",), detect_emergency_extreme_heart_rate),
)


# State → eligible-pattern-id mapping (per spec "Pattern gating by state" table).
# v3 patterns: recovery_score_drop and circadian_drift require wrist data, so
# they're watch_on only. training_load_imbalance uses apple_exercise_time which
# falls back to phone-side counting when the Watch is off, so it stays eligible
# in partial mode too.
# Emergency-tier: cardiac events stay eligible in partial because the Watch
# may still be intermittently logging events even when continuous wrist
# metrics are stale. The other emergency detectors require continuous wrist
# data and are watch_on only.
STATE_ELIGIBILITY = {
    "watch_on": {pat.id for pat in PATTERN_REGISTRY},
    "partial": {
        "low_recovery", "possible_illness", "overtraining", "deconditioning",
        "hrv_trend_down", "gait_anomaly", "training_load_imbalance",
        "emergency_cardiac_event",
        # sickness_signal self-gates per-metric staleness internally, so it can
        # safely fire in partial mode on whatever subset of wrist data is fresh.
        "sickness_signal",
    },
    "watch_off": {"gait_anomaly"},
}


def _required_metrics_fresh(findings: dict, requires: tuple[str, ...]) -> bool:
    for metric in requires:
        if (findings.get(metric) or {}).get("stale", False):
            return False
    return True


def detect_all(findings: dict, *, gated_by: str) -> list[dict]:
    """Run every detector eligible for the current state, skipping any whose
    required metrics are stale. Returns the list of fired patterns."""
    eligible = STATE_ELIGIBILITY.get(gated_by, set())
    fired: list[dict] = []
    for pat in PATTERN_REGISTRY:
        if pat.id not in eligible:
            continue
        if not _required_metrics_fresh(findings, pat.requires_metrics):
            continue
        result = pat.detector(findings)
        if result is not None:
            fired.append(result)
    return fired


def anti_spam_filter(patterns: list[dict], *, prior_jsonl_3d: list[dict]) -> list[dict]:
    """Mark patterns as suppressed if the same id fired (unsuppressed) within
    the prior 3 JSONL entries AND severity has not escalated.

    Mutates pattern dicts in place by setting `suppressed_by="3d_cooldown"`
    when applicable; returns the same list.

    Emergency-tier patterns are exempt: a persisting acute clinical signal is
    not noise to be deduplicated, it's a signal that the situation hasn't
    resolved. They always pass through.
    """
    for pattern in patterns:
        if pattern.get("severity") in ("emergency", "sickness"):
            continue
        prior_severities = []
        for entry in prior_jsonl_3d:
            for prior_pat in entry.get("_patterns", []) or []:
                if prior_pat.get("id") != pattern["id"]:
                    continue
                if prior_pat.get("suppressed_by"):
                    continue  # ignore already-suppressed priors
                prior_severities.append(prior_pat.get("severity", "low"))
        if not prior_severities:
            continue
        max_prior = max(prior_severities, key=lambda s: SEVERITY_RANK.get(s, 0))
        if SEVERITY_RANK.get(pattern["severity"], 0) <= SEVERITY_RANK.get(max_prior, 0):
            pattern["suppressed_by"] = "3d_cooldown"
    return patterns
