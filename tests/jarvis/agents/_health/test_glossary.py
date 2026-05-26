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


def test_format_for_prompt_filters_stale_metrics_by_default():
    """Metrics with stale=True are dropped — the glossary should only
    document data the LLM is allowed to cite."""
    from jarvis.agents._health.glossary import format_for_prompt

    findings = {
        "step_count": {"recent_mean": 7500, "stale": False},  # phone, fresh
        "resting_heart_rate": {"recent_mean": 0, "stale": True},  # watch, stale
        "heart_rate_variability": {"recent_mean": 0, "stale": True},  # watch, stale
        "_data_state": {"current": "watch_off"},
    }
    block = format_for_prompt(findings)

    assert "Step count" in block
    assert "Resting heart rate" not in block
    assert "Heart rate variability" not in block


def test_format_for_prompt_include_stale_opt_in():
    """Callers that want the full glossary regardless of freshness can pass
    include_stale=True (used by diagnostic / debug paths)."""
    from jarvis.agents._health.glossary import format_for_prompt

    findings = {
        "step_count": {"recent_mean": 7500, "stale": False},
        "resting_heart_rate": {"recent_mean": 0, "stale": True},
    }
    block = format_for_prompt(findings, include_stale=True)

    assert "Step count" in block
    assert "Resting heart rate" in block


def test_format_for_prompt_returns_empty_when_all_metrics_stale():
    """When every metric in findings is stale, the glossary block is empty."""
    from jarvis.agents._health.glossary import format_for_prompt

    findings = {
        "resting_heart_rate": {"recent_mean": 0, "stale": True},
        "heart_rate_variability": {"recent_mean": 0, "stale": True},
    }
    assert format_for_prompt(findings) == ""
