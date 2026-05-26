"""Tests for patterns module."""
from __future__ import annotations


def test_severity_ordering():
    from jarvis.agents._health import patterns as p

    assert p.SEVERITY_RANK["low"] < p.SEVERITY_RANK["moderate"] < p.SEVERITY_RANK["high"]


def test_pattern_registry_has_all_v2_entries():
    from jarvis.agents._health import patterns as p

    v2 = {
        "low_recovery", "deconditioning", "possible_illness", "overtraining", "hrv_trend_down",
        "sleep_debt", "respiratory_anomaly", "cardio_fitness_decline", "gait_anomaly", "cumulative_strain",
    }
    assert v2.issubset({pat.id for pat in p.PATTERN_REGISTRY})


def test_pattern_registry_has_all_v3_entries():
    from jarvis.agents._health import patterns as p

    v3 = {"recovery_score_drop", "training_load_imbalance", "circadian_drift"}
    assert v3.issubset({pat.id for pat in p.PATTERN_REGISTRY})


def test_pattern_registry_each_declares_requires_metrics():
    from jarvis.agents._health import patterns as p

    # Most patterns must declare requires_metrics. The sickness_signal detector
    # is an intentional exception: it self-gates per-metric staleness internally
    # so it can fire on a partial subset of fresh signals.
    self_gating = {"sickness_signal"}
    for pat in p.PATTERN_REGISTRY:
        if pat.id in self_gating:
            continue
        assert pat.requires_metrics, f"{pat.id} must declare requires_metrics"


def test_pattern_registry_each_declares_severity():
    from jarvis.agents._health import patterns as p

    for pat in p.PATTERN_REGISTRY:
        assert pat.severity in ("low", "moderate", "high", "sickness", "emergency")


def _f(z_scores=None, pcts=None, recent_means=None):
    """Build a findings dict from per-metric z, pct, recent_mean dicts."""
    z_scores = z_scores or {}
    pcts = pcts or {}
    recent_means = recent_means or {}
    metrics = set(z_scores) | set(pcts) | set(recent_means)
    return {
        m: {
            "z_score": z_scores.get(m, 0.0),
            "trend_pct_change_14d": pcts.get(m, 0.0),
            "recent_mean": recent_means.get(m, 0.0),
            "stale": False,
        }
        for m in metrics
    }


def test_low_recovery_fires_when_hrv_down_and_rhr_up():
    from jarvis.agents._health import patterns as p

    f = _f(z_scores={"heart_rate_variability": -1.2, "resting_heart_rate": 1.1})
    out = p.detect_low_recovery(f)
    assert out is not None
    assert out["id"] == "low_recovery"
    assert out["severity"] == "moderate"


def test_low_recovery_silent_when_only_hrv_down():
    from jarvis.agents._health import patterns as p

    f = _f(z_scores={"heart_rate_variability": -1.5, "resting_heart_rate": 0.0})
    assert p.detect_low_recovery(f) is None


def test_deconditioning_fires_when_all_three_activity_down_15pct():
    from jarvis.agents._health import patterns as p

    f = _f(pcts={"step_count": -0.20, "active_energy": -0.18, "apple_exercise_time": -0.16})
    assert p.detect_deconditioning(f)["id"] == "deconditioning"


def test_deconditioning_silent_when_only_two_metrics_down():
    from jarvis.agents._health import patterns as p

    f = _f(pcts={"step_count": -0.20, "active_energy": -0.18, "apple_exercise_time": -0.05})
    assert p.detect_deconditioning(f) is None


def test_possible_illness_fires_with_three_signals():
    from jarvis.agents._health import patterns as p

    f = _f(z_scores={"resting_heart_rate": 1.6, "heart_rate_variability": -1.2, "step_count": -1.1})
    assert p.detect_possible_illness(f)["id"] == "possible_illness"


def test_overtraining_fires_when_hrv_down_energy_up():
    from jarvis.agents._health import patterns as p

    f = _f(z_scores={"heart_rate_variability": -1.1, "active_energy": 1.2})
    assert p.detect_overtraining(f)["id"] == "overtraining"


def test_hrv_trend_down_fires_at_minus_15_pct_not_minus_10():
    from jarvis.agents._health import patterns as p

    # -10% should NOT fire (above the new threshold of -15%)
    f10 = _f(pcts={"heart_rate_variability": -0.10})
    assert p.detect_hrv_trend_down(f10) is None
    # -16% should fire
    f16 = _f(pcts={"heart_rate_variability": -0.16})
    assert p.detect_hrv_trend_down(f16)["id"] == "hrv_trend_down"


def test_sleep_debt_fires_on_3_short_nights():
    from jarvis.agents._health import patterns as p

    # 3 nights below 6.5h
    f = {"sleep_analysis": {
        "stale": False,
        "z_score": 0.0,
        "trend_pct_change_14d": 0.0,
        "recent_mean": 0.0,
        "recent_3d_values": [5.5, 6.0, 6.2],
        "trend_14d_avg_hours": 7.5,
    }}
    out = p.detect_sleep_debt(f)
    assert out["id"] == "sleep_debt"


def test_sleep_debt_fires_on_14d_avg_drop_15pct():
    from jarvis.agents._health import patterns as p

    f = {"sleep_analysis": {
        "stale": False,
        "trend_pct_change_14d": -0.16,
        "recent_3d_values": [7.5, 7.8, 7.6],
        "trend_14d_avg_hours": 6.5,
    }}
    assert p.detect_sleep_debt(f)["id"] == "sleep_debt"


def test_sleep_debt_silent_when_one_short_night_only():
    from jarvis.agents._health import patterns as p

    f = {"sleep_analysis": {
        "stale": False,
        "trend_pct_change_14d": -0.05,
        "recent_3d_values": [5.0, 7.5, 7.6],
        "trend_14d_avg_hours": 7.4,
    }}
    assert p.detect_sleep_debt(f) is None


def test_respiratory_anomaly_fires_on_3_consecutive_low_spo2_nights():
    from jarvis.agents._health import patterns as p

    f = {
        "blood_oxygen_saturation": {"stale": False, "recent_3d_z_scores": [-1.6, -1.7, -1.8]},
        "respiratory_rate": {"stale": False, "recent_3d_z_scores": [0.0, 0.0, 0.0]},
    }
    out = p.detect_respiratory_anomaly(f)
    assert out["id"] == "respiratory_anomaly"
    assert out["severity"] == "high"


def test_respiratory_anomaly_fires_on_3_consecutive_high_resp_rate_nights():
    from jarvis.agents._health import patterns as p

    f = {
        "blood_oxygen_saturation": {"stale": False, "recent_3d_z_scores": [0.0, 0.0, 0.0]},
        "respiratory_rate": {"stale": False, "recent_3d_z_scores": [1.6, 1.7, 1.8]},
    }
    assert p.detect_respiratory_anomaly(f)["id"] == "respiratory_anomaly"


def test_respiratory_anomaly_silent_on_single_night():
    from jarvis.agents._health import patterns as p

    f = {
        "blood_oxygen_saturation": {"stale": False, "recent_3d_z_scores": [-1.6, 0.0, 0.0]},
        "respiratory_rate": {"stale": False, "recent_3d_z_scores": [0.0, 0.0, 0.0]},
    }
    assert p.detect_respiratory_anomaly(f) is None


def test_cardio_fitness_decline_fires_on_5pct_drop_over_90d():
    from jarvis.agents._health import patterns as p

    f = {"vo2_max": {"stale": False, "trend_pct_change_90d": -0.06, "recent_mean": 38.0}}
    out = p.detect_cardio_fitness_decline(f)
    assert out["id"] == "cardio_fitness_decline"
    assert out["severity"] == "low"


def test_cardio_fitness_decline_silent_at_3pct_drop():
    from jarvis.agents._health import patterns as p

    f = {"vo2_max": {"stale": False, "trend_pct_change_90d": -0.03}}
    assert p.detect_cardio_fitness_decline(f) is None


def test_gait_anomaly_fires_on_3_days_high_asymmetry():
    from jarvis.agents._health import patterns as p

    f = {
        "walking_asymmetry_percentage": {"stale": False, "recent_3d_z_scores": [2.1, 2.2, 2.3]},
        "walking_speed": {"stale": False, "recent_3d_z_scores": [0.0, 0.0, 0.0]},
    }
    out = p.detect_gait_anomaly(f)
    assert out["id"] == "gait_anomaly"
    assert out["severity"] == "low"


def test_gait_anomaly_silent_on_single_day():
    from jarvis.agents._health import patterns as p

    f = {
        "walking_asymmetry_percentage": {"stale": False, "recent_3d_z_scores": [2.1, 0.0, 0.0]},
        "walking_speed": {"stale": False, "recent_3d_z_scores": [0.0, 0.0, 0.0]},
    }
    assert p.detect_gait_anomaly(f) is None


def test_cumulative_strain_fires_when_all_three_conditions():
    from jarvis.agents._health import patterns as p

    f = {
        "sleep_analysis": {"stale": False, "trend_7d_avg_hours": 6.0},
        "heart_rate_variability": {"stale": False, "z_score": -1.2},
        "active_energy": {"stale": False, "z_score": 0.7},
    }
    assert p.detect_cumulative_strain(f)["id"] == "cumulative_strain"


def test_cumulative_strain_silent_when_sleep_adequate():
    from jarvis.agents._health import patterns as p

    f = {
        "sleep_analysis": {"stale": False, "trend_7d_avg_hours": 7.5},
        "heart_rate_variability": {"stale": False, "z_score": -1.2},
        "active_energy": {"stale": False, "z_score": 0.7},
    }
    assert p.detect_cumulative_strain(f) is None


def test_recovery_score_drop_fires_when_composite_under_minus_one():
    from jarvis.agents._health import patterns as p

    # hrv_z=-1.5, rhr_z=+1.2 -> rhr_axis=-1.2, sleep_recent=5h -> sleep_axis=-2
    # composite = (-1.5 + -1.2 + -2.0) / 3 = -1.57 -> high
    f = {
        "heart_rate_variability": {"stale": False, "z_score": -1.5},
        "resting_heart_rate": {"stale": False, "z_score": 1.2},
        "sleep_analysis": {"stale": False, "recent_mean": 5.0},
    }
    out = p.detect_recovery_score_drop(f)
    assert out is not None
    assert out["id"] == "recovery_score_drop"
    assert out["severity"] == "high"


def test_recovery_score_drop_silent_when_metrics_neutral():
    from jarvis.agents._health import patterns as p

    f = {
        "heart_rate_variability": {"stale": False, "z_score": 0.2},
        "resting_heart_rate": {"stale": False, "z_score": -0.1},
        "sleep_analysis": {"stale": False, "recent_mean": 7.5},
    }
    assert p.detect_recovery_score_drop(f) is None


def test_training_load_overreach_fires_above_1_5():
    from jarvis.agents._health import patterns as p

    f = {
        "apple_exercise_time": {
            "stale": False,
            "acute_chronic_ratio": 1.7,
            "acute_7d_total_minutes": 420,
            "chronic_28d_avg_weekly_minutes": 247,
        }
    }
    out = p.detect_training_load_imbalance(f)
    assert out is not None
    assert out["id"] == "training_load_overreach"


def test_training_load_detraining_fires_below_0_8():
    from jarvis.agents._health import patterns as p

    f = {
        "apple_exercise_time": {
            "stale": False,
            "acute_chronic_ratio": 0.6,
            "acute_7d_total_minutes": 90,
            "chronic_28d_avg_weekly_minutes": 150,
        }
    }
    out = p.detect_training_load_imbalance(f)
    assert out is not None
    assert out["id"] == "training_load_detraining"


def test_training_load_silent_in_normal_range():
    from jarvis.agents._health import patterns as p

    f = {
        "apple_exercise_time": {"stale": False, "acute_chronic_ratio": 1.0}
    }
    assert p.detect_training_load_imbalance(f) is None


def test_training_load_silent_when_ratio_missing():
    """Insufficient history → ratio is None → no false alert."""
    from jarvis.agents._health import patterns as p

    f = {"apple_exercise_time": {"stale": False, "acute_chronic_ratio": None}}
    assert p.detect_training_load_imbalance(f) is None


def test_circadian_drift_fires_when_stdev_above_1_5h():
    from jarvis.agents._health import patterns as p

    f = {
        "sleep_analysis": {
            "stale": False,
            "stdev_7d_hours": 1.8,
            "recent_7d_values": [5.0, 8.5, 6.0, 9.0, 5.5, 8.0, 6.5],
        }
    }
    out = p.detect_circadian_drift(f)
    assert out is not None
    assert out["id"] == "circadian_drift"


def test_circadian_drift_silent_when_stdev_below_threshold():
    from jarvis.agents._health import patterns as p

    f = {
        "sleep_analysis": {
            "stale": False,
            "stdev_7d_hours": 0.6,
            "recent_7d_values": [7.0, 7.5, 7.0, 7.2, 6.8, 7.4, 7.1],
        }
    }
    assert p.detect_circadian_drift(f) is None


# === Emergency-tier tests ===


def test_severity_rank_includes_emergency():
    from jarvis.agents._health import patterns as p

    assert p.SEVERITY_RANK["emergency"] > p.SEVERITY_RANK["high"]


def test_emergency_cardiac_event_fires_on_any_count():
    from jarvis.agents._health import patterns as p

    f = {
        "irregular_heart_rhythm_event": {"stale": False, "latest_day_count": 1},
        "high_heart_rate_event": {"stale": False, "latest_day_count": 0},
        "low_heart_rate_event": {"stale": False, "latest_day_count": 0},
    }
    out = p.detect_emergency_cardiac_event(f)
    assert out is not None
    assert out["severity"] == "emergency"
    assert out["id"] == "emergency_cardiac_event"


def test_emergency_cardiac_event_silent_when_zero():
    from jarvis.agents._health import patterns as p

    f = {
        "irregular_heart_rhythm_event": {"stale": False, "latest_day_count": 0},
        "high_heart_rate_event": {"stale": False, "latest_day_count": 0},
        "low_heart_rate_event": {"stale": False, "latest_day_count": 0},
    }
    assert p.detect_emergency_cardiac_event(f) is None


def test_emergency_severe_respiratory_fires_on_spo2_two_severe_nights():
    from jarvis.agents._health import patterns as p

    f = {
        "blood_oxygen_saturation": {
            "stale": False,
            "recent_3d_z_scores": [-1.0, -2.8, -3.1],  # 2 severe nights
        },
        "respiratory_rate": {"stale": False, "recent_mean": 16.0, "z_score": 0.5},
    }
    out = p.detect_emergency_severe_respiratory(f)
    assert out is not None
    assert out["severity"] == "emergency"


def test_emergency_severe_respiratory_fires_on_high_resp_rate_sustained():
    from jarvis.agents._health import patterns as p

    f = {
        "blood_oxygen_saturation": {"stale": False, "recent_3d_z_scores": [0.0, 0.1, -0.2]},
        "respiratory_rate": {"stale": False, "recent_mean": 22.0, "z_score": 2.3},
    }
    out = p.detect_emergency_severe_respiratory(f)
    assert out is not None


def test_emergency_severe_respiratory_silent_on_single_bad_night():
    from jarvis.agents._health import patterns as p

    f = {
        "blood_oxygen_saturation": {
            "stale": False,
            "recent_3d_z_scores": [-0.5, -0.8, -2.7],  # only 1 severe night
        },
        "respiratory_rate": {"stale": False, "recent_mean": 15.0, "z_score": 0.0},
    }
    assert p.detect_emergency_severe_respiratory(f) is None


def test_emergency_systemic_inflammation_fires_on_quad_signal():
    from jarvis.agents._health import patterns as p

    f = {
        "resting_heart_rate": {"stale": False, "z_score": 2.4},
        "heart_rate_variability": {"stale": False, "z_score": -2.3},
        "blood_oxygen_saturation": {
            "stale": False,
            "recent_3d_z_scores": [-1.7, -1.6, -1.8],
        },
        "respiratory_rate": {"stale": False, "z_score": 1.8},
    }
    out = p.detect_emergency_systemic_inflammation(f)
    assert out is not None
    assert out["severity"] == "emergency"


def test_emergency_systemic_inflammation_silent_when_only_three_signals():
    from jarvis.agents._health import patterns as p

    f = {
        "resting_heart_rate": {"stale": False, "z_score": 2.4},
        "heart_rate_variability": {"stale": False, "z_score": -2.3},
        "blood_oxygen_saturation": {
            "stale": False,
            "recent_3d_z_scores": [-1.7, -1.6, -1.8],
        },
        "respiratory_rate": {"stale": False, "z_score": 0.5},  # not elevated
    }
    assert p.detect_emergency_systemic_inflammation(f) is None


def test_emergency_extreme_heart_rate_fires_on_sustained_high():
    from jarvis.agents._health import patterns as p

    f = {
        "resting_heart_rate": {
            "stale": False,
            "recent_mean": 115.0,
            "baseline_mean": 62.0,
        }
    }
    out = p.detect_emergency_extreme_heart_rate(f)
    assert out is not None
    assert out["evidence"]["direction"] == "high"


def test_emergency_extreme_heart_rate_fires_on_sharp_low_drop():
    """45 bpm with a normal 60 bpm baseline = real drop, not athlete bradycardia."""
    from jarvis.agents._health import patterns as p

    f = {
        "resting_heart_rate": {
            "stale": False,
            "recent_mean": 43.0,
            "baseline_mean": 60.0,
        }
    }
    out = p.detect_emergency_extreme_heart_rate(f)
    assert out is not None
    assert out["evidence"]["direction"] == "low"


def test_emergency_extreme_heart_rate_silent_for_athlete_baseline():
    """Athlete with low RHR baseline shouldn't trigger the low-HR emergency."""
    from jarvis.agents._health import patterns as p

    f = {
        "resting_heart_rate": {
            "stale": False,
            "recent_mean": 44.0,
            "baseline_mean": 45.0,
        }
    }
    assert p.detect_emergency_extreme_heart_rate(f) is None


def test_emergency_extreme_heart_rate_silent_in_normal_range():
    from jarvis.agents._health import patterns as p

    f = {
        "resting_heart_rate": {
            "stale": False,
            "recent_mean": 62.0,
            "baseline_mean": 60.0,
        }
    }
    assert p.detect_emergency_extreme_heart_rate(f) is None


def test_anti_spam_does_not_suppress_emergency():
    """Emergency-tier patterns must always fire, even if same id fired yesterday."""
    from jarvis.agents._health import patterns as p

    prior_jsonl = [{
        "_patterns": [{
            "id": "emergency_cardiac_event",
            "severity": "emergency",
        }],
    }]
    current = [{"id": "emergency_cardiac_event", "severity": "emergency"}]
    out = p.anti_spam_filter(current, prior_jsonl_3d=prior_jsonl)
    assert "suppressed_by" not in out[0]


def test_detect_all_returns_only_eligible_patterns_for_state():
    from jarvis.agents._health import patterns as p

    # watch_off — only gait_anomaly eligible (its requires_metrics are iPhone-derived)
    f = {
        "walking_asymmetry_percentage": {"stale": False, "recent_3d_z_scores": [2.5, 2.5, 2.5]},
        "walking_speed": {"stale": False, "recent_3d_z_scores": [0.0, 0.0, 0.0]},
        # All wrist metrics stale
        "heart_rate_variability": {"stale": True, "z_score": -2.0},
        "resting_heart_rate": {"stale": True, "z_score": 2.0},
    }
    fired = p.detect_all(f, gated_by="watch_off")
    ids = {pat["id"] for pat in fired}
    assert ids == {"gait_anomaly"}


def test_detect_all_skips_patterns_with_stale_required_metrics():
    from jarvis.agents._health import patterns as p

    # heart_rate_variability is stale → low_recovery should not fire even if
    # the z values look anomalous
    f = {
        "heart_rate_variability": {"stale": True, "z_score": -1.5},
        "resting_heart_rate": {"stale": False, "z_score": 1.5},
    }
    fired = p.detect_all(f, gated_by="watch_on")
    assert all(pat["id"] != "low_recovery" for pat in fired)


def test_detect_all_returns_all_eligible_in_watch_on():
    from jarvis.agents._health import patterns as p

    # Only HRV trend down condition met; nothing else
    f = {"heart_rate_variability": {"stale": False, "trend_pct_change_14d": -0.20, "z_score": 0.0}}
    fired = p.detect_all(f, gated_by="watch_on")
    assert {pat["id"] for pat in fired} == {"hrv_trend_down"}


def test_anti_spam_marks_repeat_within_3d_as_suppressed():
    from jarvis.agents._health import patterns as p

    today_pattern = {"id": "low_recovery", "severity": "moderate", "headline": "x", "evidence": {}}
    prior = [
        {"_run_at": "2026-05-09T12:00:00Z",
         "_patterns": [{"id": "low_recovery", "severity": "moderate"}]},
    ]
    out = p.anti_spam_filter([today_pattern], prior_jsonl_3d=prior)
    assert out[0].get("suppressed_by") == "3d_cooldown"


def test_anti_spam_allows_severity_escalation():
    from jarvis.agents._health import patterns as p

    today_pattern = {"id": "low_recovery", "severity": "high", "headline": "x", "evidence": {}}
    prior = [
        {"_run_at": "2026-05-09T12:00:00Z",
         "_patterns": [{"id": "low_recovery", "severity": "moderate"}]},
    ]
    out = p.anti_spam_filter([today_pattern], prior_jsonl_3d=prior)
    assert "suppressed_by" not in out[0]


def test_anti_spam_ignores_already_suppressed_priors():
    from jarvis.agents._health import patterns as p

    today_pattern = {"id": "low_recovery", "severity": "moderate", "headline": "x", "evidence": {}}
    prior = [
        {"_run_at": "2026-05-09T12:00:00Z",
         "_patterns": [{"id": "low_recovery", "severity": "moderate", "suppressed_by": "3d_cooldown"}]},
    ]
    out = p.anti_spam_filter([today_pattern], prior_jsonl_3d=prior)
    # No unsuppressed prior fire → today should fire
    assert "suppressed_by" not in out[0]


# === Sickness-tier tests ===


def _fresh_finding(z_score: float = 0.0, recent_mean: float = 0.0, **extra) -> dict:
    """Build a non-stale per-metric finding."""
    return {"z_score": z_score, "recent_mean": recent_mean, "stale": False, **extra}


def _stale_finding() -> dict:
    return {"z_score": 0.0, "recent_mean": 0.0, "stale": True}


class TestSicknessSignal:
    def test_fires_with_rhr_up_and_hrv_down(self):
        from jarvis.agents._health.patterns import detect_sickness_signal
        findings = {
            "resting_heart_rate": _fresh_finding(z_score=1.5, recent_mean=78.0),
            "heart_rate_variability": _fresh_finding(z_score=-1.2, recent_mean=32.0),
            "basal_body_temperature": _stale_finding(),
            "respiratory_rate": _stale_finding(),
            "walking_heart_rate_average": _stale_finding(),
            "blood_oxygen_saturation": _stale_finding(),
        }
        result = detect_sickness_signal(findings)
        assert result is not None
        assert result["severity"] == "sickness"
        assert result["id"] == "sickness_signal"
        assert "rhr_up" in result["evidence"]["signals_fired"]
        assert "hrv_down" in result["evidence"]["signals_fired"]

    def test_does_not_fire_with_only_one_signal(self):
        from jarvis.agents._health.patterns import detect_sickness_signal
        findings = {
            "resting_heart_rate": _fresh_finding(z_score=1.5),
            "heart_rate_variability": _fresh_finding(z_score=0.0),
            "basal_body_temperature": _stale_finding(),
            "respiratory_rate": _stale_finding(),
            "walking_heart_rate_average": _stale_finding(),
            "blood_oxygen_saturation": _stale_finding(),
        }
        assert detect_sickness_signal(findings) is None

    def test_does_not_fire_when_all_metrics_stale(self):
        from jarvis.agents._health.patterns import detect_sickness_signal
        findings = {
            "resting_heart_rate": _stale_finding(),
            "heart_rate_variability": _stale_finding(),
            "basal_body_temperature": _stale_finding(),
            "respiratory_rate": _stale_finding(),
            "walking_heart_rate_average": _stale_finding(),
            "blood_oxygen_saturation": _stale_finding(),
        }
        assert detect_sickness_signal(findings) is None

    def test_wrist_temp_elevation_counts_as_signal(self):
        from jarvis.agents._health.patterns import detect_sickness_signal
        findings = {
            "resting_heart_rate": _fresh_finding(z_score=1.2),
            "basal_body_temperature": _fresh_finding(z_score=1.5, recent_mean=0.4),
            "heart_rate_variability": _stale_finding(),
            "respiratory_rate": _stale_finding(),
            "walking_heart_rate_average": _stale_finding(),
            "blood_oxygen_saturation": _stale_finding(),
        }
        result = detect_sickness_signal(findings)
        assert result is not None
        assert "wrist_temp_up" in result["evidence"]["signals_fired"]
        assert "rhr_up" in result["evidence"]["signals_fired"]

    def test_spo2_absolute_threshold_triggers_signal(self):
        """SpO2 recent_mean below 94% counts as the spo2_down signal even
        if z-score is small (absolute clinical threshold)."""
        from jarvis.agents._health.patterns import detect_sickness_signal
        findings = {
            "resting_heart_rate": _fresh_finding(z_score=1.2),
            "blood_oxygen_saturation": _fresh_finding(
                z_score=-0.4, recent_mean=93.0, recent_3d_z_scores=[-0.4, 0.0, 0.2],
            ),
            "heart_rate_variability": _stale_finding(),
            "basal_body_temperature": _stale_finding(),
            "respiratory_rate": _stale_finding(),
            "walking_heart_rate_average": _stale_finding(),
        }
        result = detect_sickness_signal(findings)
        assert result is not None
        assert "spo2_down" in result["evidence"]["signals_fired"]

    def test_spo2_sustained_dip_triggers_signal(self):
        """SpO2 with 2+ of last 3 days at z <= -0.8 counts as spo2_down even
        if absolute level is above 94%."""
        from jarvis.agents._health.patterns import detect_sickness_signal
        findings = {
            "resting_heart_rate": _fresh_finding(z_score=1.2),
            "blood_oxygen_saturation": _fresh_finding(
                z_score=-0.6, recent_mean=96.0, recent_3d_z_scores=[-1.0, -0.9, 0.0],
            ),
            "heart_rate_variability": _stale_finding(),
            "basal_body_temperature": _stale_finding(),
            "respiratory_rate": _stale_finding(),
            "walking_heart_rate_average": _stale_finding(),
        }
        result = detect_sickness_signal(findings)
        assert result is not None
        assert "spo2_down" in result["evidence"]["signals_fired"]

    def test_registered_in_pattern_registry(self):
        from jarvis.agents._health.patterns import PATTERN_REGISTRY
        ids = [p.id for p in PATTERN_REGISTRY]
        assert "sickness_signal" in ids

    def test_severity_rank_includes_sickness(self):
        from jarvis.agents._health.patterns import SEVERITY_RANK
        assert "sickness" in SEVERITY_RANK
        # Between high and emergency
        assert SEVERITY_RANK["high"] < SEVERITY_RANK["sickness"] < SEVERITY_RANK["emergency"]

    def test_eligible_in_watch_on_and_partial(self):
        from jarvis.agents._health.patterns import STATE_ELIGIBILITY
        assert "sickness_signal" in STATE_ELIGIBILITY["watch_on"]
        assert "sickness_signal" in STATE_ELIGIBILITY["partial"]

    def test_anti_spam_exempts_sickness(self):
        """Sickness patterns bypass the 3-day cooldown like emergencies do."""
        from jarvis.agents._health.patterns import anti_spam_filter
        pattern = {"id": "sickness_signal", "severity": "sickness"}
        prior = [
            {"_patterns": [{"id": "sickness_signal", "severity": "sickness"}]},
            {"_patterns": [{"id": "sickness_signal", "severity": "sickness"}]},
            {"_patterns": [{"id": "sickness_signal", "severity": "sickness"}]},
        ]
        filtered = anti_spam_filter([pattern], prior_jsonl_3d=prior)
        assert filtered[0].get("suppressed_by") is None
