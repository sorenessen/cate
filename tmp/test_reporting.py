from cate.reporting import build_executive_summary, render_report_md

def t_flow_ok():
    summary = {"steps": 3, "failures": 0, "avg_latency_ms": 123.4}
    signals = {"kind": "http-flow", "ok": True, "severity": "none", "notes": []}
    ex = build_executive_summary(kind="http-flow", env="dev", summary=summary, signals=signals)
    assert "successfully" in ex["headline"].lower()
    assert ex["confidence"] == "high"

def t_flow_fail_redirect():
    summary = {"steps": 1, "failures": 1, "avg_latency_ms": 88.8}
    signals = {
        "kind": "http-flow",
        "ok": False,
        "severity": "high",
        "notes": ["flow_failures_seen", "all_steps_failed"],
        "top_failure": {"step": "r", "expected": "[200]", "status": 302, "error": "expected status in [200], got 302"},
    }
    ex = build_executive_summary(kind="http-flow", env="dev", summary=summary, signals=signals)
    assert "top failure" in ex["headline"].lower()
    assert any("redirect" in s.lower() for s in ex["key_findings"])
    assert any("location" in s.lower() for s in ex["recommended_actions"])

def t_fuzz_fail_5xx_429():
    summary = {
        "total_payloads": 10,
        "error_count": 3,
        "error_rate": 0.3,
        "status_counts": {"200": 6, "500": 2, "429": 1, "none": 1},
        "latency": {"mean_ms": 10.1, "p99_ms": 40.2},
    }
    signals = {"kind": "http-fuzz", "ok": False, "severity": "medium", "notes": ["rate_limit_seen"]}
    ex = build_executive_summary(kind="http-fuzz", env="dev", summary=summary, signals=signals)
    assert any("5xx" in s.lower() for s in ex["key_findings"])
    assert any("429" in s.lower() for s in ex["key_findings"])
    assert any("rate" in s.lower() for s in ex["recommended_actions"])

def t_render_deterministic():
    summary = {"steps": 1, "failures": 0, "avg_latency_ms": 1.23456}
    signals = {"kind": "http-flow", "ok": True, "severity": "none", "notes": []}
    a = render_report_md(
        kind="http-flow", env="dev", summary=summary, signals=signals,
        summary_json_path="x.summary.json", summary_md_path="x.summary.md",
        signals_json_path="x.signals.json", signals_md_path="x.signals.md",
    )
    b = render_report_md(
        kind="http-flow", env="dev", summary=summary, signals=signals,
        summary_json_path="x.summary.json", summary_md_path="x.summary.md",
        signals_json_path="x.signals.json", signals_md_path="x.signals.md",
    )
    assert a == b

if __name__ == "__main__":
    t_flow_ok()
    t_flow_fail_redirect()
    t_fuzz_fail_5xx_429()
    t_render_deterministic()
    print("OK")
