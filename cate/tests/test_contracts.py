from __future__ import annotations

from cate.contracts import validate_summary, validate_signals, ContractError
from cate.signals import compute_signals_from_summary


def test_validate_summary_flow_ok():
    summary = {"steps": 1, "failures": 0, "avg_latency_ms": 10.0, "final_vars": {}, "fail_samples": []}
    kind, warnings = validate_summary(summary)
    assert kind == "http-flow"
    assert isinstance(warnings, list)


def test_validate_summary_fuzz_ok():
    summary = {
        "total_payloads": 1,
        "error_count": 1,
        "error_rate": 1.0,
        "status_counts": {"503": 1},
        "latency": {"p50_ms": 10.0, "p90_ms": 10.0, "p99_ms": 10.0},
    }
    kind, warnings = validate_summary(summary)
    assert kind == "http-fuzz"
    assert isinstance(warnings, list)


def test_validate_summary_ambiguous_rejected():
    # Has both flow + fuzz keys -> should raise
    summary = {
        "steps": 1, "failures": 0,
        "total_payloads": 1, "status_counts": {"200": 1},
    }
    try:
        validate_summary(summary)
        assert False, "expected ContractError"
    except ContractError:
        pass


def test_compute_signals_flow_fail_has_top_failure_if_samples_present():
    summary = {
        "steps": 1,
        "failures": 1,
        "avg_latency_ms": 100.0,
        "final_vars": {},
        "fail_samples": [
            {"step": "r", "status": 302, "expected": "[200]", "error": "expected status in [200], got 302"}
        ],
    }
    signals = compute_signals_from_summary(summary)
    warnings = validate_signals(signals)
    assert signals["kind"] == "http-flow"
    assert signals["ok"] is False
    assert signals["severity"] in ("medium", "high")
    assert signals.get("top_failure") is not None
    assert isinstance(warnings, list)


def test_compute_signals_fuzz_503_is_high():
    summary = {
        "total_payloads": 1,
        "error_count": 1,
        "error_rate": 1.0,
        "status_counts": {"503": 1},
        "latency": {"p50_ms": 100.0, "p90_ms": 100.0, "p99_ms": 100.0},
    }
    signals = compute_signals_from_summary(summary)
    warnings = validate_signals(signals)
    assert signals["kind"] == "http-fuzz"
    assert signals["ok"] is False
    assert signals["severity"] == "high"
    assert "server_errors_seen" in signals["notes"]
    assert isinstance(warnings, list)
