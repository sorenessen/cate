from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any
from statistics import mean
from datetime import datetime, timezone
import sys

from cate import __version__
from .engine import run_job
from .logging_utils import write_results_jsonl
from .models import JobConfig, Target
from .profiles import load_profile, ProfileNotFound
from .flows import load_flow, load_flows, run_flow, FlowNotFound, _apply_template_functions
from cate.flows import load_flows, load_flow, FlowNotFound, lint_flows


# Simple ANSI color helpers
_RESET = "\033[0m"
_BOLD = "\033[1m"
_FG_CYAN = "\033[36m"
_FG_GREEN = "\033[32m"
_FG_YELLOW = "\033[33m"
_FG_RED = "\033[31m"
_FG_MAGENTA = "\033[35m"

_SUPPORTS_COLOR = sys.stdout.isatty()


def _color(text: str, code: str) -> str:
    if not _SUPPORTS_COLOR:
        return text
    return f"{code}{text}{_RESET}"


def parse_headers(header_list: Optional[List[str]]) -> Dict[str, str]:
    """
    Parse repeated --header 'Key: Value' into a dict.
    Ignores malformed entries.
    """
    headers: Dict[str, str] = {}
    if not header_list:
        return headers

    for raw in header_list:
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key:
            headers[key] = value
    return headers

def parse_vars(var_list: Optional[List[str]]) -> Dict[str, str]:
    """
    Parse repeated --var key=value into a dict.
    Ignores malformed entries.
    """
    vars_map: Dict[str, str] = {}
    if not var_list:
        return vars_map

    for raw in var_list:
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            vars_map[key] = value
    return vars_map


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cate",
        description="Calypso Automated Testing Engine",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    http_parser = subparsers.add_parser(
        "http-fuzz",
        help="Run a simple HTTP fuzz / brute-force job",
    )

    # NOTE: url/wordlist optional when using --profile; we enforce in code
    http_parser.add_argument(
        "--url",
        required=False,
        default=None,
        help="Target URL. Use {payload} as a placeholder in the query or path.",
    )
    http_parser.add_argument(
        "--method",
        default="GET",
        help="HTTP method (GET, POST, etc.). Default: GET",
    )
    http_parser.add_argument(
        "--wordlist",
        required=False,
        default=None,
        help="Path to wordlist file (one payload per line).",
    )
    http_parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent requests. Default: 10",
    )
    http_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds. Default: 10",
    )
    http_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional JSONL output path for results.",
    )
    http_parser.add_argument(
        "--placeholder",
        type=str,
        default="{payload}",
        help="Placeholder string in URL or body. Default: {payload}",
    )

    http_parser.add_argument(
        "--body-template",
        type=str,
        default=None,
        help=(
            "Optional body/template string. "
            "Use {payload} as a placeholder. "
            "Example: 'user=admin&pass={payload}' or "
            '\'{"user":"admin","pass":"{payload}"}\''
        ),
    )

    http_parser.add_argument(
        "--header",
        action="append",
        default=None,
        help=(
            "Optional HTTP header, can be used multiple times. "
            'Example: --header "Authorization: Bearer TOKEN" '
            '--header "X-Env: dev"'
        ),
    )

    http_parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Optional profile name to load from profiles.toml "
             "(e.g. 'delphonix-login-dev').",
    )

    # Safety controls
    http_parser.add_argument(
        "--max-rps",
        type=float,
        default=5.0,
        help="Maximum requests per second (global). Default: 5.0",
    )
    http_parser.add_argument(
        "--stop-on-error-rate",
        type=float,
        default=0.5,
        help="Stop if recent error fraction exceeds this (0–1). Default: 0.5",
    )
    http_parser.add_argument(
        "--env",
        type=str,
        default="dev",
        choices=["dev", "stage", "prod"],
        help="Environment label for this target (dev, stage, prod). Default: dev",
    )
    http_parser.add_argument(
        "--i-understand-prod",
        action="store_true",
        help="Required when --env prod is used, to acknowledge live-target testing.",
    )
    http_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be executed, but do not send any HTTP requests.",
    )

    http_flow_parser = subparsers.add_parser(
        "http-flow",
        help="Run a multi-step HTTP flow defined in flows.toml",
    )
    http_flow_parser.add_argument(
        "--flows-file",
        type=str,
        default="flows.toml",
        help="Path to flows TOML file (default: flows.toml in current directory).",
    )
    http_flow_parser.add_argument(
        "--flow",
        type=str,
        required=False,
        help="Flow name from flows.toml (e.g. delphonix-login-sequence).",
    )
    http_flow_parser.add_argument(
        "--list",
        action="store_true",
        help="List available flows from flows.toml and exit.",
    )
    http_flow_parser.add_argument(
        "--lint",
        action="store_true",
        help="Validate flows TOML structure/types and exit (no network). Non-zero on errors.",
    )
    http_flow_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds. Default: 10",
    )
    http_flow_parser.add_argument(
        "--max-rps",
        type=float,
        default=2.0,
        help="Max requests per second across the flow. Default: 2.0",
    )
    http_flow_parser.add_argument(
        "--env",
        type=str,
        default="dev",
        choices=["dev", "stage", "prod"],
        help="Environment label for this flow. Default: dev",
    )
    http_flow_parser.add_argument(
        "--i-understand-prod",
        action="store_true",
        help="Required when --env prod is used, to acknowledge live-target testing.",
    )
    http_flow_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the flow steps without sending any HTTP requests.",
    )
    http_flow_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Optional output prefix for logs. "
            "If set, writes <prefix>.jsonl and <prefix>.summary.md"
        ),
    )
    http_flow_parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help=(
            "Stop the entire flow as soon as any step fails. "
            "Per-step `stop_on_fail = true` in flows.toml still applies; "
            "this flag adds a global fail-fast mode."
        ),
    )
    http_flow_parser.add_argument(
        "--continue-on-fail",
        action="store_true",
        help=(
            "Force the flow to continue through all steps even if some fail. "
            "Overrides per-step `stop_on_fail = true` flags."
        ),
    )
    http_flow_parser.add_argument(
        "--vars-dump",
        action="store_true",
        help=(
            "After the flow finishes, print extracted variables from "
            "`extract_regex`/`store_as` steps."
        ),
    )
    http_flow_parser.add_argument(
        "--save-body",
        action="store_true",
        help=(
            "When steps fail, write their response bodies to disk next to "
            "the JSONL/summary files."
        ),
    )
    http_flow_parser.add_argument(
        "--mode",
        type=str,
        default="default",
        choices=["default", "recon", "auth-pressure"],  # expand later
        help="Assessment mode: default | recon | auth-pressure",
    )
    http_flow_parser.add_argument(
        "--var",
        action="append",
        default=None,
        help=(
            "Set a template variable for this flow, e.g. "
            "--var username=admin --var password=secret. "
            "These become available in templates as {username}, {password}, etc."
        ),
    )

    http_flow_parser.add_argument("--no-jsonl", action="store_true", help="Do not write <output>.jsonl")
    http_flow_parser.add_argument("--no-summary-md", action="store_true", help="Do not write <output>.summary.md")
    http_flow_parser.add_argument("--no-summary-json", action="store_true", help="Do not write <output>.summary.json")
    http_flow_parser.add_argument("--quiet", action="store_true", help="Suppress 'Wrote:' line (still prints errors)")


    return parser


def summarize_results(results) -> None:
    """
    Print a quick summary grouping by (status_code, content_length)
    and showing sample payloads. Helps spot outliers fast.
    """
    groups: Dict[Tuple[Optional[int], Optional[int]], List[str]] = defaultdict(list)

    for r in results:
        key = (r.status_code, r.content_length)
        groups[key].append(r.payload)

    if not groups:
        print("[CATE] No results to summarize.")
        return

    print("\n[CATE] Response groups (by status_code, content_length):")

    sorted_groups = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)

    for (status_code, content_length), payloads in sorted_groups:
        count = len(payloads)
        status_str = "None" if status_code is None else str(status_code)
        size_str = "None" if content_length is None else str(content_length)

        samples = payloads[:5]
        sample_str = ", ".join(samples)
        more = "" if count <= 5 else f" (+{count - 5} more)"

        print(
            f"  - status={status_str}, size={size_str} bytes: "
            f"{count} payload(s). Samples: [{sample_str}]{more}"
        )

    print("\n[CATE] Potential outliers (rare response shapes):")
    for (status_code, content_length), payloads in sorted_groups:
        if len(payloads) <= 3:
            status_str = "None" if status_code is None else str(status_code)
            size_str = "None" if content_length is None else str(content_length)
            print(
                f"  * status={status_str}, size={size_str} bytes → "
                f"{len(payloads)} payload(s): {payloads}"
            )

def write_flow_logs(
    output_prefix: str,
    results: List[Dict[str, Any]],
    env: Optional[str] = None,
    initial_vars: Optional[Dict[str, Any]] = None,
    save_body: bool = False,
    write_jsonl: bool = True,
    write_summary_md: bool = True,
    write_summary_json: bool = True,
) -> List[Path]:
    """
    Write flow execution logs:

      - <output_prefix>.jsonl       : one JSON object per step
      - <output_prefix>.summary.md  : human-readable Markdown summary
      - <output_prefix>.summary.json: machine-readable summary
      - <output_prefix>.stepN_<name>.body.txt : (optional) response bodies
        for failing steps when save_body=True
    """
    written: List[Path] = []

    out = Path(output_prefix)

    # If the user passed a filename ending in .jsonl, keep it.
    # Otherwise treat as prefix and append .jsonl
    if out.suffix.lower() == ".jsonl":
        jsonl_path = out
    else:
        jsonl_path = Path(f"{output_prefix}.jsonl")

    # For summaries, always hang them off the jsonl base name
    summary_md_path = jsonl_path.with_suffix(".summary.md")
    summary_json_path = jsonl_path.with_suffix(".summary.json")

    # Ensure parent directory exists for ALL outputs we might write
    for p, enabled in [
        (jsonl_path, write_jsonl),
        (summary_md_path, write_summary_md),
        (summary_json_path, write_summary_json),
    ]:
        if enabled:
            p.parent.mkdir(parents=True, exist_ok=True)


    # JSONL: one line per step (unchanged)
    if write_jsonl:
        with jsonl_path.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        written.append(jsonl_path)


    # --- Pretty Markdown summary ---

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
    if passed:
        lines.append("# ✅ Flow Passed")
    else:
        lines.append("# ❌ Flow Failed")
    lines.append("")

    # 2. Overview
    lines.append("## Overview\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Steps | {total} |")
    lines.append(f"| Failures | {failures} |")
    lines.append(f"| Avg latency | {avg_ms:.2f} ms |")

    # Optional env / CLI vars context
    if env is not None:
        lines.append(f"| Env | {env} |")

    if initial_vars:
        # Only show keys, not values (avoid leaking secrets)
        var_keys = ", ".join(sorted(str(k) for k in initial_vars.keys()))
        lines.append(f"| Seeded vars | {var_keys} |")

    lines.append("")

    # 3. Failing steps (if any)
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
            lines.append(
                f"| {step} | {method} | {url} | {status} | {latency} | {error} |"
            )
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

    # 5. Assertion breakdown (new in v0.3.x)
    # Only render if at least one step has assertions or extracted vars.
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

        # Stable column order for common assertion names,
        # then append any uncommon ones at the end.
        common_order = [
            "status_ok",
            "latency_ok",
            "body_contains_ok",
            "body_not_contains_ok",
            "extracted_ok",
        ]
        extra_keys = [k for k in sorted(assertion_keys) if k not in common_order]
        ordered_keys = [k for k in common_order if k in assertion_keys] + extra_keys

        header_cols: List[str] = ["Step"]
        for k in ordered_keys:
            header_cols.append(k)
        if has_extracted:
            header_cols.extend(["extracted_var", "extracted_value"])

        # Header row
        lines.append("| " + " | ".join(header_cols) + " |")
        lines.append("|" + "|".join(["---"] * len(header_cols)) + "|")

        # Rows
        for r in results:
            row: List[str] = []
            row.append(str(r.get("step", "")))

            assertions = r.get("assertions") or {}
            for k in ordered_keys:
                val = assertions.get(k, None)
                if val is True:
                    cell = "✅"
                elif val is False:
                    cell = "❌"
                else:
                    cell = ""
                row.append(cell)

            if has_extracted:
                row.append(str(r.get("extracted_var") or ""))
                row.append(str(r.get("extracted_value") or ""))

            lines.append("| " + " | ".join(row) + " |")

        lines.append("")

    # 6. Final extracted variables (if any)
    if final_vars:
        lines.append("## Extracted variables (final state)\n")
        lines.append(
            "These are the last values of any variables extracted during this flow."
        )
        lines.append("")
        lines.append("| Name | Value |")
        lines.append("|------|-------|")
        for name, value in final_vars.items():
            safe_name = str(name).replace("|", "\\|")
            safe_value = str(value).replace("|", "\\|")
            lines.append(f"| {safe_name} | {safe_value} |")
        lines.append("")

    # Flow summary JSON (compact, machine-readable)
    summary_obj: Dict[str, Any] = {
        "steps": total,
        "failures": failures,
        "avg_latency_ms": avg_ms,
        "final_vars": final_vars,
    }
    if write_summary_json:
        try:
            summary_json_path.write_text(
                json.dumps(summary_obj, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            written.append(summary_json_path)
        except Exception:
            pass


    # 7. Footer
    lines.append("---")
    lines.append("_Report generated by **CATE v0.3 — Calypso Automated Testing Engine**_")
    lines.append("")

    if write_summary_md:
        summary_md_path.write_text("\n".join(lines), encoding="utf-8")
        written.append(summary_md_path)


    # 8. Dump response bodies for failing steps
    # 8. Dump response bodies for failing steps
    if save_body:
        import re as _re

        for idx, r in enumerate(results, 1):
            if r.get("ok"):
                continue

            body = r.get("body")
            if not body:
                continue

            step_name = str(r.get("step", f"step{idx}"))
            safe_step = _re.sub(r"[^A-Za-z0-9_-]+", "_", step_name)

            body_path = Path(f"{output_prefix}.step{idx}_{safe_step}.body.txt")
            html_capture = Path(f"{output_prefix}.step{idx}_{safe_step}.body.html")

            try:
                # Ensure directory exists for body dumps
                body_path.parent.mkdir(parents=True, exist_ok=True)

                # Always write plain text body for grepping / diffing
                body_text = body if isinstance(body, str) else str(body)
                body_path.write_text(body_text, encoding="utf-8")
                written.append(body_path)

                # Best-effort HTML detection – only write .html if it looks like HTML
                lower = body_text.lower()
                if "<html" in lower or "<!doctype html" in lower:
                    html_capture.write_text(body_text, encoding="utf-8")
                    written.append(html_capture)

            except Exception:
                # best-effort; don't kill the run if body write fails
                pass


    return written


def _percentile(values: List[float], pct: float) -> Optional[float]:
    """
    Simple percentile helper: pct in [0, 100].
    Returns None if list is empty.
    """
    if not values:
        return None
    values_sorted = sorted(values)
    if len(values_sorted) == 1:
        return values_sorted[0]
    k = (pct / 100.0) * (len(values_sorted) - 1)
    i = int(k)
    f = k - i
    if i + 1 < len(values_sorted):
        return values_sorted[i] + (values_sorted[i + 1] - values_sorted[i]) * f
    return values_sorted[-1]


def build_run_summary(results, config: JobConfig) -> Dict[str, Any]:
    """
    Build a machine-readable summary dict for a given run.
    """
    total = len(results)
    error_count = 0
    latencies: List[float] = []
    status_counts: Dict[str, int] = {}
    error_examples: List[Dict[str, Any]] = []

    for r in results:
        status = r.status_code
        key = "none" if status is None else str(status)
        status_counts[key] = status_counts.get(key, 0) + 1

        if getattr(r, "error", None) or (status is not None and status >= 500):
            error_count += 1
            if len(error_examples) < 10:
                error_examples.append(
                    {
                        "payload": r.payload,
                        "status_code": status,
                        "error": getattr(r, "error", None),
                        "elapsed_ms": getattr(r, "elapsed_ms", None),
                        "timestamp": getattr(r, "timestamp", None),
                    }
                )

        elapsed = getattr(r, "elapsed_ms", None)
        if isinstance(elapsed, (int, float)):
            latencies.append(float(elapsed))

    error_rate = (error_count / total) if total > 0 else 0.0

    latency_stats: Dict[str, Optional[float]] = {}
    if latencies:
        latency_stats = {
            "count": len(latencies),
            "min_ms": min(latencies),
            "max_ms": max(latencies),
            "mean_ms": mean(latencies),
            "p50_ms": _percentile(latencies, 50),
            "p90_ms": _percentile(latencies, 90),
            "p99_ms": _percentile(latencies, 99),
        }

    target = config.target
    now = datetime.now(timezone.utc).isoformat()

    return {
        "generated_at": now,
        "target": {
            "method": getattr(target, "method", None),
            "url": getattr(target, "url", None),
        },
        "env": getattr(config, "env", getattr(config, "environment", None)),
        "wordlist": str(getattr(config, "wordlist_path", "")),
        "concurrency": config.concurrency,
        "timeout_seconds": config.timeout_seconds,
        "max_rps": config.max_rps,
        "stop_on_error_rate": config.stop_on_error_rate,
        "total_payloads": total,
        "error_count": error_count,
        "error_rate": error_rate,
        "status_counts": status_counts,
        "latency": latency_stats,
        "error_examples": error_examples,
    }


def render_markdown_summary(summary: Dict[str, Any]) -> str:
    """
    Turn the JSON summary dict into a human-readable Markdown report.
    """
    status_codes = summary.get("status_codes", {}) or {}
    error_count = int(summary.get("error_count", 0) or 0)
    error_rate = float(summary.get("error_rate", 0.0) or 0.0)

    # Treat as failure if:
    #   - any errors were recorded, OR
    #   - any status code >= 500 shows up.
    has_server_errors = any(
        (code not in (None, "None"))
        and isinstance(code, str)
        and code.isdigit()
        and int(code) >= 500
        for code in status_codes.keys()
    )

    passed = (error_count == 0) and (not has_server_errors) and (error_rate == 0.0)

    lines: List[str] = []

    # Header
    if passed:
        lines.append("# ✅ CATE Run Passed")
    else:
        lines.append("# ❌ CATE Run Failed")
    lines.append("")

    # Status Codes
    lines.append("## Status Codes\n")
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    for code, count in sorted(status_codes.items(), key=lambda kv: kv[0]):
        lines.append(f"| {code} | {count} |")
    lines.append("")

    # Latency
    latency = summary.get("latency", {}) or {}
    lines.append("## Latency (ms)\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for key in ["count", "min_ms", "max_ms", "avg_ms"]:
        if key in latency:
            lines.append(f"| {key} | {latency[key]} |")
    lines.append("")

    lines.append("---")
    lines.append("_Report generated by **CATE – Calypso Automated Testing Engine**_")
    lines.append("")

    return "\n".join(lines)


def write_run_summaries(
    output_path: Path,
    results,
    config: JobConfig,
) -> None:
    """
    Given the main JSONL output path, write:
      - <name>.summary.json
      - <name>.summary.md
    """
    summary = build_run_summary(results, config)

    json_path = output_path.with_suffix(".summary.json")
    md_path = output_path.with_suffix(".summary.md")

    try:
        import json

        json_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as exc:  # best-effort; don't kill the run
        print(_color(f"[CATE] Failed to write JSON summary: {exc}", _FG_YELLOW))

    try:
        md_text = render_markdown_summary(summary)
        md_path.write_text(md_text, encoding="utf-8")
    except Exception as exc:
        print(_color(f"[CATE] Failed to write Markdown summary: {exc}", _FG_YELLOW))


def build_effective_config(args) -> Dict[str, Any]:
    """
    Combine profile (if any) + CLI flags into a single config dict.

    Rules:
      - If --profile is provided, it supplies the baseline config.
      - CLI headers always override/extend profile headers.
      - CLI env, output, and i-understand-prod are always honored.
      - If no profile is given, URL and wordlist must be supplied via CLI.
    """
    headers_from_cli = parse_headers(args.header)
    profile_data: Dict[str, Any] | None = None

    if args.profile:
        try:
            profile_data = load_profile(args.profile)
        except FileNotFoundError as e:
            print(f"[CATE] {e}")
            raise SystemExit(1)
        except ProfileNotFound as e:
            print(f"[CATE] {e}")
            raise SystemExit(1)

    if profile_data:
        url = profile_data.get("url")
        method = profile_data.get("method", args.method)
        wordlist = profile_data.get("wordlist", args.wordlist)
        body_template = profile_data.get("body_template", args.body_template)
        placeholder = profile_data.get("placeholder", args.placeholder)
        concurrency = profile_data.get("concurrency", args.concurrency)
        timeout = profile_data.get("timeout", args.timeout)
        max_rps = profile_data.get("max_rps", args.max_rps)
        stop_on_error_rate = profile_data.get("stop_on_error_rate", args.stop_on_error_rate)
        env = profile_data.get("env", args.env)

        profile_headers = profile_data.get("headers", {})
        if not isinstance(profile_headers, dict):
            profile_headers = {}

        headers = {**profile_headers, **headers_from_cli}

        if not url:
            print("[CATE] Profile is missing 'url'.")
            raise SystemExit(1)
        if not wordlist:
            print("[CATE] Profile is missing 'wordlist' and none supplied via CLI.")
            raise SystemExit(1)

    else:
        if not args.url:
            print("[CATE] --url is required if no --profile is specified.")
            raise SystemExit(1)
        if not args.wordlist:
            print("[CATE] --wordlist is required if no --profile is specified.")
            raise SystemExit(1)

        url = args.url
        method = args.method
        wordlist = args.wordlist
        body_template = args.body_template
        placeholder = args.placeholder
        concurrency = args.concurrency
        timeout = args.timeout
        max_rps = args.max_rps
        stop_on_error_rate = args.stop_on_error_rate
        env = args.env
        headers = headers_from_cli

    return {
        "url": url,
        "method": method,
        "wordlist": wordlist,
        "body_template": body_template,
        "placeholder": placeholder,
        "concurrency": concurrency,
        "timeout": timeout,
        "max_rps": max_rps,
        "stop_on_error_rate": stop_on_error_rate,
        "env": env,
        "headers": headers,
    }


def run_http_fuzz(
    url: str,
    method: str,
    wordlist: str,
    concurrency: int,
    timeout: float,
    output: Optional[str],
    placeholder: str,
    body_template: Optional[str],
    max_rps: float,
    stop_on_error_rate: float,
    env: str,
    i_understand_prod: bool,
    dry_run: bool,
    headers: Dict[str, str],
) -> int:
    # Safety: block real prod runs without explicit flag
    if env == "prod" and not i_understand_prod and not dry_run:
        print(
            _color(
                "[CATE] Refusing to run against env=prod without --i-understand-prod flag. Aborting.",
                _FG_RED,
            )
        )
        return 1

    # DRY RUN: show what *would* happen, then exit before making any requests
    if dry_run:
        print(_color("[CATE] DRY RUN — no HTTP requests will be sent.", _FG_MAGENTA))
        print(_color(f"[CATE] Environment: {env}", _FG_CYAN))
        print(_color(f"[CATE] Target: {method.upper()} {url}", _FG_CYAN))
        print(_color(f"[CATE] Wordlist: {wordlist}", _FG_CYAN))
        print(
            _color(
                f"[CATE] Concurrency={concurrency}, "
                f"max_rps={max_rps}, stop_on_error_rate={stop_on_error_rate}",
                _FG_CYAN,
            )
        )
        if headers:
            print(_color(f"[CATE] Headers: {headers}", _FG_CYAN))
        if body_template:
            print(_color(f"[CATE] Body template: {body_template}", _FG_CYAN))
        print(_color(f"[CATE] Placeholder: {placeholder}", _FG_CYAN))
        return 0

    # Normal run header
    print(_color(f"[CATE] Environment: {env}", _FG_CYAN))
    print(
        _color(
            f"[CATE] Config: method={method}, concurrency={concurrency}, "
            f"max_rps={max_rps}, stop_on_error_rate={stop_on_error_rate}",
            _FG_CYAN,
        )
    )
    if body_template:
        print(_color(f"[CATE] Using body template: {body_template!r}", _FG_CYAN))
    if headers:
        print(_color(f"[CATE] Using headers: {headers!r}", _FG_CYAN))

    # Build job config
    target = Target(url=url, method=method, headers=headers or None)
    config = JobConfig(
        target=target,
        wordlist_path=wordlist,
        concurrency=concurrency,
        timeout_seconds=timeout,
        output_path=output,
        placeholder=placeholder,
        body_template=body_template,
        max_rps=max_rps,
        stop_on_error_rate=stop_on_error_rate,
    )

    async def _run() -> int:
        results = await run_job(config)

        output_path: Optional[Path] = None
        if output:
            output_path = Path(output)
            write_results_jsonl(output_path, results)

        total = len(results)
        errors = sum(
            1
            for r in results
            if r.error or (r.status_code is not None and r.status_code >= 500)
        )
        if errors:
            print(
                _color(
                    f"[CATE] Completed {total} payloads ({errors} errors).",
                    _FG_YELLOW,
                )
            )
        else:
            print(
                _color(
                    f"[CATE] Completed {total} payloads (0 errors).",
                    _FG_GREEN,
                )
            )

        if output_path is not None:
            print(_color(f"[CATE] Results written to {output_path}", _FG_GREEN))
            write_run_summaries(output_path, results, config)

        summarize_results(results)
        return 0


    return asyncio.run(_run())


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Handle http-fuzz -------------------------------------------------------
    if args.command == "http-fuzz":
        cfg = build_effective_config(args)
        return run_http_fuzz(
            url=cfg["url"],
            method=cfg["method"],
            wordlist=cfg["wordlist"],
            concurrency=cfg["concurrency"],
            timeout=cfg["timeout"],
            output=args.output,
            placeholder=cfg["placeholder"],
            body_template=cfg["body_template"],
            max_rps=cfg["max_rps"],
            stop_on_error_rate=cfg["stop_on_error_rate"],
            env=cfg["env"],
            i_understand_prod=args.i_understand_prod,
            dry_run=args.dry_run,
            headers=cfg["headers"],
        )

        # Handle http-flow ------------------------------------------------------
    elif args.command == "http-flow":
        # Optional: allow overriding the flows file (default handled by flows module)
        flows_path = Path(args.flows_file) if getattr(args, "flows_file", None) else None

        # Global flag conflict check
        if args.stop_on_fail and args.continue_on_fail:
            print(
                _color(
                    "[CATE] Cannot use --stop-on-fail and --continue-on-fail together.",
                    _FG_RED,
                )
            )
            return 1

        # --lint: validate flows and exit (no network)
        if getattr(args, "lint", False):
            try:
                flows, warnings, errors = lint_flows(flows_path)
            except FileNotFoundError as e:
                print(_color(f"[CATE] {e}", _FG_RED))
                return 1
            except ValueError as e:
                # TOML parse error with enhanced context
                print(_color(str(e), _FG_RED))
                return 1

            # Print override warnings (non-fatal)
            for w in warnings:
                print(_color(f"[CATE] {w}", _FG_YELLOW))

            if errors:
                print(_color("[CATE] Flow lint failed:", _FG_RED))
                for err in errors:
                    print(_color(f"  - {err}", _FG_RED))
                return 1

            print(_color("[CATE] Flow lint passed (no issues found).", _FG_GREEN))
            return 0


        # --list: enumerate flows and exit
        if args.list:
            try:
                flows = load_flows(flows_path)
            except FileNotFoundError as e:
                print(_color(f"[CATE] {e}", _FG_RED))
                return 1

            if not flows:
                print(_color("[CATE] No flows found in flows.toml.", _FG_YELLOW))
                return 0

            print(_color("[CATE] Available flows:", _FG_CYAN))
            for name, flow in flows.items():
                desc = flow.description or ""
                if desc:
                    print(f"  - {name}: {desc}")
                else:
                    print(f"  - {name}")
            return 0

        # Normal flow execution path
        if not args.flow:
            print(_color("[CATE] --flow is required unless --list is used.", _FG_RED))
            return 1

        try:
            # NOTE: now passes `path=flows_path` so `--flows-file` is honored
            flow = load_flow(args.flow, path=flows_path)
        except FileNotFoundError as e:
            print(_color(f"[CATE] {e}", _FG_RED))
            return 1
        except FlowNotFound as e:
            print(_color(f"[CATE] {e}", _FG_RED))
            return 1

        print(_color(f"[CATE] Loaded flow '{flow.name}'", _FG_CYAN))
        if flow.description:
            print(_color(f"[CATE] Description: {flow.description}", _FG_CYAN))

        print(_color("[CATE] Steps:", _FG_CYAN))
        for idx, step in enumerate(flow.steps, 1):
            line = (
                f"  {idx}. {step.name} -> {step.method} {step.url} "
                f"(capture_cookies={step.capture_cookies}, "
                f"expect_status={step.expect_status})"
            )
            print(line)

        # CLI-provided variables for template interpolation
        initial_vars: Dict[str, Any] = {}
        if getattr(args, "var", None):
            initial_vars = parse_vars(args.var)
            if initial_vars:
                print(_color(f"[CATE] Seeded flow vars: {initial_vars}", _FG_CYAN))


        # DRY RUN: show what *would* happen, including interpolated templates,
        # but do not send any HTTP requests (allowed even for prod).
        if args.dry_run:
            print(
                _color(
                    f"[CATE] DRY RUN — not executing flow (v0.3.0 stateful HTTP run) "
                    f"in env={args.env} (timeout={args.timeout}s, max_rps={args.max_rps})",
                    _FG_MAGENTA,
                )
            )

            # We only have CLI-provided vars here; no extracted vars since we don't run.
            vars_map: Dict[str, Any] = dict(initial_vars)

            print(_color("[CATE] DRY RUN request preview:", _FG_CYAN))
            for idx, step in enumerate(flow.steps, 1):
                url_template = step.url
                body_template = step.body_template
                headers_template = step.headers or {}

                # Apply template functions where possible; unknown vars stay as-is
                url_preview = _apply_template_functions(url_template, vars_map)
                body_preview = (
                    _apply_template_functions(body_template, vars_map)
                    if body_template is not None
                    else None
                )
                headers_preview = {
                    key: _apply_template_functions(value, vars_map)
                    for key, value in headers_template.items()
                }

                print(f"  Step {idx}: {step.name}")
                print(f"    {step.method} {url_preview}")
                if body_preview is not None:
                    print(f"    Body: {body_preview}")
                if headers_preview:
                    print(f"    Headers: {headers_preview}")

            return 0


        # Safety: prevent accidental prod without explicit opt-in
        if args.env == "prod" and not args.i_understand_prod:
            print(
                _color(
                    "[CATE] Refusing to run flow against env=prod without "
                    "--i-understand-prod flag. Aborting.",
                    _FG_RED,
                )
            )
            return 1

        print(
            _color(
                f"[CATE] Executing flow (v0.3.0 stateful HTTP run) in env={args.env} "
                f"(timeout={args.timeout}s, max_rps={args.max_rps})…",
                _FG_CYAN,
            )
        )

        # Global fail-fast / continue behaviour
        stop_on_first_failure = bool(args.stop_on_fail)
        ignore_step_stop_flags = bool(args.continue_on_fail)

        # CLI-provided variables for template interpolation
        initial_vars: Dict[str, Any] = {}
        if getattr(args, "var", None):
            initial_vars = parse_vars(args.var)
            if initial_vars:
                print(_color(f"[CATE] Seeded flow vars: {initial_vars}", _FG_CYAN))


        results = run_flow(
            flow=flow,
            timeout=args.timeout,
            max_rps=args.max_rps,
            stop_on_first_failure=stop_on_first_failure,
            ignore_step_stop_flags=ignore_step_stop_flags,
            initial_vars=initial_vars,
            mode=args.mode,
        )


        print("\n[CATE] Flow results:")
        failures = 0
        for r in results:
            status_label = "OK" if r["ok"] else "FAIL"
            color = _FG_GREEN if r["ok"] else _FG_RED
            status = r["status_code"] if r["status_code"] is not None else "ERR"
            line = (
                f"{r['step']}: {r['method']} {r['url']} → "
                f"status={status}, {r['elapsed_ms']:.1f} ms, {r['bytes']} bytes"
            )
            if r["error"]:
                line += f" — {r['error']}"
            print(_color(f"[{status_label}] {line}", color))
            if not r["ok"]:
                failures += 1

        # Optional: dump extracted variables (from extract_regex/store_as)
        if args.vars_dump:
            vars_map: Dict[str, Any] = {}
            for r in results:
                var_name = r.get("extracted_var")
                var_value = r.get("extracted_value")
                if var_name is not None and var_value is not None:
                    # last writer wins if the same var is extracted multiple times
                    vars_map[var_name] = var_value

            if not vars_map:
                print(_color("[CATE] No extracted variables to dump.", _FG_YELLOW))
            else:
                print(_color("[CATE] Extracted variables:", _FG_CYAN))
                for k, v in vars_map.items():
                    print(f"  - {k} = {v!r}")

        if args.output:
            written_paths = write_flow_logs(
                output_prefix=args.output,
                results=results,
                env=args.env,
                initial_vars=initial_vars,
                save_body=args.save_body,
                write_jsonl=not getattr(args, "no_jsonl", False),
                write_summary_md=not getattr(args, "no_summary_md", False),
                write_summary_json=not getattr(args, "no_summary_json", False),
            )

            if not getattr(args, "quiet", False):
                if written_paths:
                    joined = ", ".join(str(p) for p in written_paths)
                    print(_color(f"[CATE] Wrote: {joined}", _FG_CYAN))
                else:
                    print(_color("[CATE] No output files written (all outputs disabled).", _FG_YELLOW))

        if failures:
            print(
                _color(
                    f"[CATE] Flow completed with {failures} failing step(s).",
                    _FG_RED,
                )
            )
            return 1

        print(_color("[CATE] Flow completed successfully.", _FG_GREEN))
        return 0



    # Fallback --------------------------------------------------------------
    parser.error("Unknown command")
    return 1



if __name__ == "__main__":
    raise SystemExit(main())
