import json
from copy import deepcopy

from cate.signals import compute_signals_from_summary
from cate.contracts import validate_signals


def test_http_flow_signals_deterministic_for_same_summary():
    summary = {
        "kind": "http-flow",
        "steps": 3,
        "failures": 1,
        "avg_latency_ms": 123.4,
        "final_vars": {"token": "abc"},
        "fail_samples": [
            {"step": "login", "status": 302, "expected": "[200]", "error": "expected status in [200], got 302"}
        ],
    }

    a = compute_signals_from_summary(deepcopy(summary))
    b = compute_signals_from_summary(deepcopy(summary))

    # validate shape
    validate_signals(a)
    validate_signals(b)

    # deterministic JSON representation
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_http_fuzz_signals_deterministic_for_same_summary():
    summary = {
        "kind": "http-fuzz",
        "total_payloads": 5,
        "error_count": 1,
        "error_rate": 0.2,
        "status_counts": {"200": 4, "503": 1},
        "latency": {"p50_ms": 10.0, "p90_ms": 20.0, "p99_ms": 30.0},
        "error_examples": [
            {"payload": "' OR 1=1 --", "status_code": 503, "error": None, "elapsed_ms": 12.3, "timestamp": "2025-01-01T00:00:00Z"}
        ],
    }

    a = compute_signals_from_summary(deepcopy(summary))
    b = compute_signals_from_summary(deepcopy(summary))

    validate_signals(a)
    validate_signals(b)

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
