# cate/signals.py
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def compute_signals_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute lightweight "signals" from either:
      - http-fuzz summary (your build_run_summary output)
      - http-flow summary (your write_flow_logs summary_obj)

    Returns a JSON-serializable dict meant for:
      - quick triage
      - trend tracking
      - later scoring/risk heuristics
    """
    # Detect which summary shape we received
    is_flow = "steps" in summary and "failures" in summary

    if is_flow:
        steps = _safe_int(summary.get("steps")) or 0
        failures = _safe_int(summary.get("failures")) or 0
        avg_latency_ms = _safe_float(summary.get("avg_latency_ms")) or 0.0

        failure_rate = (failures / steps) if steps else 0.0

        notes: List[str] = []
        if failures > 0:
            notes.append("flow_failures_seen")
        if steps > 0 and failures == steps and failures > 0:
            notes.append("all_steps_failed")
        if avg_latency_ms >= 1000:
            notes.append("slow_flow")

        ok = failures == 0

        # Severity that's predictable:
        # - none if clean
        # - high if everything failed OR failure_rate >= 0.5
        # - medium if some failures
        if ok:
            severity = "none"
        elif (steps > 0 and failures == steps) or (steps > 0 and failure_rate >= 0.5):
            severity = "high"
        else:
            severity = "medium"

        fail_samples = summary.get("fail_samples") or []
        top_failure = fail_samples[0] if fail_samples else None


        return {
            "kind": "http-flow",
            "ok": ok,
            "severity": severity,
            "counts": {
                "steps": steps,
                "failures": failures,
                "failure_rate": round(float(failure_rate), 6),
            },
            "latency": {
                "avg_ms": avg_latency_ms,
            },
            "notes": notes,
            "final_vars": summary.get("final_vars") or {},
            "top_failure": top_failure,
        }


    # http-fuzz summary shape (build_run_summary)
    total = _safe_int(summary.get("total_payloads")) or 0
    error_count = _safe_int(summary.get("error_count")) or 0
    error_rate = _safe_float(summary.get("error_rate")) or 0.0
    status_counts = summary.get("status_counts") or {}
    latency = summary.get("latency") or {}

    # Basic derived signals
    has_429 = str(429) in status_counts
    has_401 = str(401) in status_counts
    has_403 = str(403) in status_counts
    has_5xx = any(str(k).isdigit() and int(k) >= 500 for k in status_counts.keys())

    p50 = _safe_float(latency.get("p50_ms"))
    p90 = _safe_float(latency.get("p90_ms"))
    p99 = _safe_float(latency.get("p99_ms"))

    ok = (error_count == 0) and (not has_5xx) and (error_rate == 0.0)

    notes: List[str] = []
    if has_429:
        notes.append("rate_limited_429_seen")
    if has_401 or has_403:
        notes.append("auth_denied_seen")
    if has_5xx:
        notes.append("server_errors_seen")
    if total > 0 and error_rate >= 0.5:
        notes.append("high_error_rate")

    # Simple severity (you can evolve this later)
    severity = "none"
    if not ok:
        if has_5xx or error_rate >= 0.5:
            severity = "high"
        elif has_429 or has_401 or has_403:
            severity = "medium"
        else:
            severity = "low"

    return {
        "kind": "http-fuzz",
        "ok": ok,
        "severity": severity,
        "counts": {
            "total_payloads": total,
            "error_count": error_count,
            "error_rate": error_rate,
            "status_counts": status_counts,
        },
        "latency": {
            "p50_ms": p50,
            "p90_ms": p90,
            "p99_ms": p99,
        },
        "notes": notes,
    }

from .contracts import ContractError, validate_signals

def finalize_signals(signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize + contract-check signals.
    Call this after adding optional fields (top_trigger/top_failure).
    Applies mode-aware severity policy (default/recon/auth-pressure).
    """
    if not isinstance(signals, dict):
        raise ContractError("signals must be a dict")

    signals.setdefault("notes", [])
    signals.setdefault("counts", {})
    signals.setdefault("latency", {})
    signals.setdefault("severity", "none")
    signals.setdefault("ok", False)
    signals.setdefault("mode", "default")  # <— add

    # Normalize core fields
    signals["severity"] = str(signals.get("severity", "none")).lower().strip()
    signals["ok"] = bool(signals.get("ok", False))
    signals["mode"] = str(signals.get("mode", "default")).lower().strip()  # <— add

    notes = signals.get("notes")
    if notes is None:
        notes_list: List[str] = []
    elif isinstance(notes, list):
        notes_list = [str(n) for n in notes]
    else:
        notes_list = [str(notes)]
    signals["notes"] = notes_list

    # ---- Mode-aware severity policy ----
    mode = signals["mode"]
    sev = signals["severity"]
    hard_fail = (signals["ok"] is False)

    levels = ["none", "low", "medium", "high"]

    def _idx(s: str) -> int:
        return levels.index(s) if s in levels else 0

    def bump(s: str, delta: int) -> str:
        i = _idx(s) + delta
        if i < 0:
            i = 0
        if i >= len(levels):
            i = len(levels) - 1
        return levels[i]

    def clamp_max(s: str, max_level: str) -> str:
        return levels[min(_idx(s), _idx(max_level))]

    notes_lc = " ".join(n.lower() for n in notes_list)

    if mode == "recon":
        # Recon should stay informational unless we truly failed.
        if not hard_fail:
            new_sev = clamp_max(sev, "medium")
            if new_sev != sev:
                notes_list.append("mode=recon: severity clamped")
                sev = new_sev

    elif mode == "auth-pressure":
        # Auth-pressure is allowed to be more sensitive.
        bump_markers = (
            "rate", "429", "lockout", "unauthorized", "forbidden",
            "captcha", "waf", "csrf", "token", "auth",
        )
        if any(m in notes_lc for m in bump_markers):
            new_sev = bump(sev, +1)
            if new_sev != sev:
                notes_list.append("mode=auth-pressure: severity bumped")
                sev = new_sev

    signals["severity"] = sev
    signals["notes"] = notes_list
    # ---- end policy ----

    validate_signals(signals)  # raises ContractError if invalid
    return signals

