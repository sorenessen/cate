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

def write_flow_summary_md(
    path: Path,
    results: Sequence[Mapping[str, Any]],
    env: Optional[str] = None,
    initial_vars: Optional[Dict[str, Any]] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_flow_summary_md(results=results, env=env, initial_vars=initial_vars),
        encoding="utf-8",
    )

