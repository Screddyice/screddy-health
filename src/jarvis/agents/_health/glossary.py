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


def format_for_prompt(findings: dict, *, include_stale: bool = False) -> str:
    """Render a Markdown glossary block covering only the metrics that appear
    in `findings` AND have a glossary entry. Returns "" when there is no
    overlap. Keys starting with `_` (metadata like `_data_state`, `_run_at`)
    are skipped.

    By default, metrics with `stale=True` are excluded — the glossary only
    documents what the LLM should reference in the digest. Pass
    `include_stale=True` to override (used for diagnostic / debug callers
    that want the full glossary regardless of freshness).

    Output shape (Markdown):

        ### Metric glossary (for citations below)
        - **Resting heart rate (RHR)** (bpm) — Lowest sustained heart rate... healthy 55–75. Concerning: sustained increase of 8–10 bpm...
        - **Heart rate variability (HRV)** (ms) — Beat-to-beat variation... Concerning: a 20–30% drop sustained over 3+ days...
    """
    if not findings:
        return ""
    lines: list[str] = []
    for key, blob in findings.items():
        if key.startswith("_"):
            continue
        entry = METRIC_GLOSSARY.get(key)
        if not entry:
            continue
        if not include_stale and isinstance(blob, dict) and blob.get("stale", False):
            continue
        lines.append(
            f"- **{entry['label']}** ({entry['units']}) — {entry['definition']} "
            f"Healthy: {entry['healthy_range']} Concerning: {entry['concerning']}"
        )
    if not lines:
        return ""
    return "### Metric glossary (for citations below)\n" + "\n".join(lines)
