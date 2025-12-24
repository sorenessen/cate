# cate/contracts.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class ContractError(ValueError):
    pass


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_summary(summary: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Returns: (kind, warnings)
    Raises: ContractError if invalid
    """
    if not isinstance(summary, dict):
        raise ContractError("summary must be a dict")

    # kind is optional in fuzz summaries today; flow we set explicitly.
    kind = str(summary.get("kind") or "unknown")

    warnings: List[str] = []

    # Detect summary type by shape
    is_flow = "steps" in summary and "failures" in summary
    is_fuzz = "total_payloads" in summary and "status_counts" in summary

    if is_flow and is_fuzz:
        raise ContractError("summary looks like both flow and fuzz (ambiguous shape)")

    if not is_flow and not is_fuzz:
        warnings.append("unknown_summary_shape")

    if is_flow:
        if not isinstance(summary.get("steps"), int):
            raise ContractError("flow summary requires int 'steps'")
        if not isinstance(summary.get("failures"), int):
            raise ContractError("flow summary requires int 'failures'")
        if "avg_latency_ms" in summary and summary["avg_latency_ms"] is not None and not _is_num(summary["avg_latency_ms"]):
            raise ContractError("flow summary 'avg_latency_ms' must be number or null")
        if "final_vars" in summary and summary["final_vars"] is not None and not isinstance(summary["final_vars"], dict):
            raise ContractError("flow summary 'final_vars' must be dict or null")
        if "fail_samples" in summary and summary["fail_samples"] is not None and not isinstance(summary["fail_samples"], list):
            raise ContractError("flow summary 'fail_samples' must be list or null")

        kind = "http-flow"

    if is_fuzz:
        if not isinstance(summary.get("total_payloads"), int):
            raise ContractError("fuzz summary requires int 'total_payloads'")
        if not isinstance(summary.get("error_count"), int):
            raise ContractError("fuzz summary requires int 'error_count'")
        if "error_rate" in summary and not _is_num(summary.get("error_rate")):
            raise ContractError("fuzz summary requires numeric 'error_rate'")
        if not isinstance(summary.get("status_counts"), dict):
            raise ContractError("fuzz summary requires dict 'status_counts'")
        if "latency" in summary and summary["latency"] is not None and not isinstance(summary["latency"], dict):
            raise ContractError("fuzz summary 'latency' must be dict or null")

        kind = "http-fuzz"

    return kind, warnings


def validate_signals(signals: Dict[str, Any]) -> List[str]:
    """
    Returns warnings. Raises ContractError if invalid.
    """
    if not isinstance(signals, dict):
        raise ContractError("signals must be a dict")

    required = ["kind", "ok", "severity", "counts", "latency", "notes"]
    for k in required:
        if k not in signals:
            raise ContractError(f"signals missing required key '{k}'")

    if signals["kind"] not in ("http-fuzz", "http-flow"):
        raise ContractError("signals.kind must be 'http-fuzz' or 'http-flow'")

    if not isinstance(signals["ok"], bool):
        raise ContractError("signals.ok must be bool")

    if not isinstance(signals["severity"], str):
        raise ContractError("signals.severity must be str")

    if signals["severity"] not in ("none", "low", "medium", "high"):
        raise ContractError("signals.severity must be one of: none, low, medium, high")

    # Validate containers BEFORE key membership checks
    if not isinstance(signals["counts"], dict):
        raise ContractError("signals.counts must be dict")

    if not isinstance(signals["latency"], dict):
        raise ContractError("signals.latency must be dict")

    if not isinstance(signals["notes"], list):
        raise ContractError("signals.notes must be list")

    # Kind-specific counts contract
    kind = signals["kind"]
    if kind == "http-flow":
        for k in ("steps", "failures", "failure_rate"):
            if k not in signals["counts"]:
                raise ContractError(f"http-flow signals.counts missing '{k}'")
    else:  # http-fuzz
        for k in ("total_payloads", "error_count", "error_rate", "status_counts"):
            if k not in signals["counts"]:
                raise ContractError(f"http-fuzz signals.counts missing '{k}'")

    warnings: List[str] = []

    # Optional extras (warn-only)
    if "top_trigger" in signals and signals["top_trigger"] is not None and not isinstance(signals["top_trigger"], str):
        warnings.append("top_trigger_not_string")

    if "top_failure" in signals and signals["top_failure"] is not None and not isinstance(signals["top_failure"], dict):
        warnings.append("top_failure_not_dict")

    return warnings

# ---------------------------------------------------------
# Canonical signal pipeline
# ---------------------------------------------------------

from typing import Dict, Any
from .signals import compute_signals_from_summary


def build_and_validate_signals(
    summary: Dict[str, Any],
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Canonical pipeline:
      summary
        → validate_summary
        → compute_signals_from_summary
        → validate_signals

    If strict=True:
      - any contract warning raises ContractError
    """

    kind, warnings = validate_summary(summary)
    summary["kind"] = kind  # normalize explicitly

    if strict and warnings:
        raise ContractError(f"summary contract warnings: {warnings}")

    signals = compute_signals_from_summary(summary)

    signal_warnings = validate_signals(signals)
    if strict and signal_warnings:
        raise ContractError(f"signals contract warnings: {signal_warnings}")

    return signals


