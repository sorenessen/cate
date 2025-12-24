# cate/logging_utils.py
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Optional, Dict, List, Any, Mapping, Sequence

from .models import Result

def render_flow_summary_md(
    results: Sequence[Mapping[str, Any]],
    env: Optional[str] = None,
    initial_vars: Optional[Dict[str, Any]] = None,
) -> str:
    total = len(results)
    failing = [r for r in results if not r.get("ok")]
    failures = len(failing)
    passed = (total > 0 and failures == 0)
    avg_ms = (
        sum(r.get("elapsed_ms", 0.0) for r in results) / total
        if total > 0
        else 0.0
    )

    # Final vars: last value for each extracted_var across the flow
    final_vars: Dict[str, Any] = {}
    for r in results:
        var_name = r.get("extracted_var")
        var_value = r.get("extracted_value")
        if var_name is not None and var_value is not None:
            final_vars[var_name] = var_value

    lines: List[str] = []

    # 1. Header
    lines.append("# ✅ Flow Passed" if passed else "# ❌ Flow Failed")
    lines.append("")

    # 2. Overview
    lines.append("## Overview\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Steps | {total} |")
    lines.append(f"| Failures | {failures} |")
    lines.append(f"| Avg latency | {avg_ms:.2f} ms |")

    if env is not None:
        lines.append(f"| Env | {env} |")

    if initial_vars:
        # Only show keys, not values (avoid leaking secrets)
        var_keys = ", ".join(sorted(str(k) for k in initial_vars.keys()))
        lines.append(f"| Seeded vars | {var_keys} |")

    lines.append("")

    # 3. Failing steps
    if failing:
        lines.append("## Failing Steps\n")
        lines.append("| Step | Method | URL | Status | Time (ms) | Error |")
        lines.append("|------|--------|-----|--------|-----------|--------|")
        for r in failing:
            step = r.get("step", "")
            method = r.get("method", "")
            url = str(r.get("url", "")).replace("|", "\\|")
            status = r.get("status_code", "None")
            latency = r.get("elapsed_ms", "–")
            error = str(r.get("error") or "").replace("|", "\\|")
            lines.append(f"| {step} | {method} | {url} | {status} | {latency} | {error} |")
        lines.append("")

    # 4. All steps
    if results:
        lines.append("## All Steps\n")
        lines.append("| Step | Method | URL | Status | OK | Latency (ms) | Bytes | Error |")
        lines.append("|------|--------|-----|--------|----|-------------:|------:|-------|")
        for r in results:
            step = r.get("step", "")
            method = r.get("method", "")
            url = str(r.get("url", "")).replace("|", "\\|")
            status = r.get("status_code", "None")
            ok = "✅" if r.get("ok") else "❌"
            latency = f"{r.get('elapsed_ms', 0.0):.1f}"
            size = r.get("bytes", 0)
            error = str(r.get("error") or "").replace("|", "\\|")
            lines.append(
                f"| {step} | {method} | {url} | {status} | {ok} | {latency} | {size} | {error} |"
            )
        lines.append("")

    # Recon observations (if present)
    recon_steps = [r for r in results if isinstance(r.get("recon"), dict) and r.get("recon")]
    if recon_steps:
        lines.append("")
        lines.append("## Recon Observations")
        for s in recon_steps:
            step_name = s.get("step", "")
            recon = s.get("recon", {})

            lines.append("")
            lines.append(f"### Step {step_name}")

            chain = recon.get("redirect_chain")
            if chain:
                lines.append("")
                lines.append("**Redirect chain:**")
                for hop in chain:
                    if isinstance(hop, dict):
                        st = hop.get("status")
                        loc = hop.get("location")
                        lines.append(f"- {st} → {loc}" if loc else f"- {st}")
                    else:
                        lines.append(f"- {hop}")

            headers = recon.get("headers")
            if isinstance(headers, dict) and headers:
                lines.append("")
                lines.append("**Observed headers:**")
                for k, v in headers.items():
                    lines.append(f"- `{k}`: `{v}`")

            body_hash = recon.get("body_hash")
            if body_hash:
                lines.append("")
                lines.append(f"**Body fingerprint:** `{body_hash}`")

    # 5. Assertion breakdown
    assertion_keys: set[str] = set()
    has_extracted = False
    for r in results:
        assertions = r.get("assertions") or {}
        assertion_keys.update(assertions.keys())
        if r.get("extracted_var") is not None or r.get("extracted_value") is not None:
            has_extracted = True

    if assertion_keys or has_extracted:
        lines.append("## Assertion breakdown\n")
        lines.append(
            "This section shows per-step assertion results and any variables that were "
            "extracted from response bodies."
        )
        lines.append("")

        common_order = ["status_ok", "latency_ok", "body_contains_ok", "body_not_contains_ok", "extracted_ok"]
        extra_keys = [k for k in sorted(assertion_keys) if k not in common_order]
        ordered_keys = [k for k in common_order if k in assertion_keys] + extra_keys

        header_cols: List[str] = ["Step"] + ordered_keys
        if has_extracted:
            header_cols.extend(["extracted_var", "extracted_value"])

        lines.append("| " + " | ".join(header_cols) + " |")
        lines.append("|" + "|".join(["---"] * len(header_cols)) + "|")

        for r in results:
            row: List[str] = [str(r.get("step", ""))]
            assertions = r.get("assertions") or {}
            for k in ordered_keys:
                val = assertions.get(k, None)
                row.append("✅" if val is True else ("❌" if val is False else ""))
            if has_extracted:
                row.append(str(r.get("extracted_var") or ""))
                row.append(str(r.get("extracted_value") or ""))
            lines.append("| " + " | ".join(row) + " |")

        lines.append("")

    # 6. Final extracted variables
    if final_vars:
        lines.append("## Extracted variables (final state)\n")
        lines.append("These are the last values of any variables extracted during this flow.")
        lines.append("")
        lines.append("| Name | Value |")
        lines.append("|------|-------|")
        for name, value in final_vars.items():
            safe_name = str(name).replace("|", "\\|")
            safe_value = str(value).replace("|", "\\|")
            lines.append(f"| {safe_name} | {safe_value} |")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append("_Report generated by **CATE v0.3 — Calypso Automated Testing Engine**_")
    lines.append("")

    return "\n".join(lines)


def write_results_jsonl(path: Path, results: Iterable[Result]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            data = asdict(r)
            # ISO format for datetime
            data["timestamp"] = r.timestamp.isoformat() + "Z"
            f.write(json.dumps(data) + "\n")


def write_signals_json(signals: Dict[str, Any], output_path: str) -> str:
    """
    Writes signals next to the summary artifacts.
    output_path is the JSONL path (same one you already use).
    Returns the signals json path.
    """
    base = Path(output_path)
    signals_path = str(base.with_suffix(".signals.json"))
    Path(signals_path).write_text(json.dumps(signals, indent=2, sort_keys=True), encoding="utf-8")
    return signals_path


def write_signals_md(signals: Dict[str, Any], output_path: str) -> str:
    """
    Writes <output_path>.signals.md.

    Supports BOTH schemas:
      A) legacy: {"verdict": {...}, "signals": [ {id,severity,message}, ... ]}
      B) current: {"kind","ok","severity","counts","latency","notes", ...}
    """
    base = Path(output_path)
    md_path = str(base.with_suffix(".signals.md"))

    lines: List[str] = []

    # --- Schema A (legacy) ---
    if isinstance(signals.get("verdict"), dict) and isinstance(signals.get("signals"), list):
        v = signals.get("verdict", {})
        lines.append(f"# CATE Signals — {str(v.get('severity','info')).upper()}\n")
        for s in signals.get("signals", []):
            lines.append(f"## {s.get('id')} ({s.get('severity')})")
            lines.append(s.get("message", ""))
            lines.append("")
        Path(md_path).write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return md_path

    # --- Schema B (your current 0.4.1 signals.json) ---
    severity = str(signals.get("severity", "info")).upper()
    ok = bool(signals.get("ok", False))
    kind = signals.get("kind", "run")
    env = signals.get("env")

    title_bits = [f"# CATE Signals — {severity}"]
    if env:
        title_bits[0] += f" ({env})"
    lines.append(title_bits[0])
    lines.append("")
    lines.append(f"- **Kind:** `{kind}`")
    lines.append(f"- **OK:** `{ok}`")

    notes = signals.get("notes") or []
    if notes:
        lines.append(f"- **Notes:** {', '.join(f'`{n}`' for n in notes)}")
    lines.append("")

    tf = signals.get("top_failure")
    if isinstance(tf, dict) and tf:
        lines.append("")
        lines.append("## Top failure")
        step = tf.get("step")
        exp = tf.get("expected")
        got = tf.get("status")
        err = tf.get("error")
        if step is not None:
            lines.append(f"- Step: **{step}**")
        if exp is not None:
            lines.append(f"- Expected: **{exp}**")
        if got is not None:
            lines.append(f"- Got: **{got}**")
        if err:
            lines.append(f"- Error: `{err}`")

    tt = signals.get("top_trigger")
    if tt is not None and signals.get("kind") == "http-fuzz":
        lines.append("")
        lines.append("## Top trigger")
        lines.append(f"- Payload: `{tt}`")


    counts = signals.get("counts") or {}
    kind = signals.get("kind") or "run"

    lines.append("## Counts\n")

    if kind == "http-flow":
        lines.append(f"- Steps: **{counts.get('steps', 0)}**")
        lines.append(f"- Failures: **{counts.get('failures', 0)}**")
        if "failure_rate" in counts:
            lines.append(f"- Failure rate: **{counts.get('failure_rate', 0)}**")
    else:
        # http-fuzz (current labels)
        lines.append(f"- Total payloads: **{counts.get('total_payloads', 0)}**")
        lines.append(f"- Error count: **{counts.get('error_count', 0)}**")
        lines.append(f"- Error rate: **{counts.get('error_rate', 0)}**")

        status_counts = counts.get("status_counts") or {}
        if status_counts:
            lines.append("")
            lines.append("### Status codes\n")
            for k in sorted(status_counts.keys(), key=lambda x: int(x) if str(x).isdigit() else str(x)):
                lines.append(f"- `{k}`: **{status_counts[k]}**")

    lines.append("")


    latency = signals.get("latency") or {}
    if any(latency.get(k) is not None for k in ("p50_ms", "p90_ms", "p99_ms")):
        lines.append("## Latency (ms)\n")
        for k in ("p50_ms", "p90_ms", "p99_ms"):
            if latency.get(k) is not None:
                lines.append(f"- `{k}`: **{latency[k]}**")
        lines.append("")

    # Optional: if you later add groups/outliers into signals.json, this will render them.
    groups = signals.get("groups") or []
    if groups:
        lines.append("## Response groups\n")
        for g in groups:
            lines.append(
                f"- status={g.get('status_code', g.get('status'))}, "
                f"size={g.get('content_length', g.get('size'))} "
                f"→ **{g.get('count', 0)}**"
            )
        lines.append("")

    outliers = signals.get("outliers") or []
    if outliers:
        lines.append("## Potential outliers\n")
        for o in outliers:
            lines.append(
                f"- status={o.get('status_code', o.get('status'))}, "
                f"size={o.get('content_length', o.get('size'))} "
                f"→ **{o.get('count', 0)}**"
            )
        lines.append("")

    Path(md_path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return md_path
