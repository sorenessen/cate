from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

from cate import __version__
from .contracts import ContractError, validate_signals, validate_summary
from .engine import run_job
from .flows import FlowNotFound, _apply_template_functions, load_flow, load_flows, run_flow
from .logging_utils import (
    render_flow_summary_md,
    write_evidence_manifest,
    write_results_jsonl,
    write_signals_json,
    write_signals_md,
)
from .models import JobConfig, Target
from .profiles import ProfileNotFound, load_profile
from .reporting import write_report_md
from .signals import finalize_signals

# -----------------------------
# Simple ANSI color helpers
# -----------------------------
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


def _print_signal_verdict(signals: dict) -> None:
    sev_raw = str(signals.get("severity", "none")).lower()
    sev = sev_raw.upper()
    kind = signals.get("kind", "run")
    notes = signals.get("notes") or []

    notes_str = ", ".join(notes[:3]) if notes else "none"

    # label + color by severity (demo-friendly)
    if sev_raw in ("none", "low"):
        label = "OK"
        color = _FG_GREEN
    elif sev_raw == "medium":
        label = "WARN"
        color = _FG_YELLOW
    else:
        label = "ALERT"
        color = _FG_RED

    extra = ""

    tf = signals.get("top_failure")
    if isinstance(tf, dict) and tf:
        step = tf.get("step")
        exp = tf.get("expected")
        got = tf.get("status")
        if step is not None or exp is not None or got is not None:
            extra += f", step={step}, expected={exp}, got={got}"

    tt = signals.get("top_trigger")
    if kind == "http-fuzz" and tt is not None:
        extra += f", trigger={tt!r}"

    print(
        _color(
            f"[CATE] Signal verdict: {sev} ({label}) — kind={kind}, notes={notes_str}{extra}",
            color,
        )
    )


# -----------------------------
# Bundling
# -----------------------------
def bundle_from_manifest(manifest_path: Path, bundle_path: Optional[Path] = None) -> Path:
    """
    Create a zip bundle containing the manifest + all artifact paths listed in it.
    Returns the bundle zip path.

    Default output name:
      <prefix>.manifest.json -> <prefix>.bundle.zip
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    if bundle_path is None:
        name = manifest_path.name
        if name.endswith(".manifest.json"):
            out_name = name.replace(".manifest.json", ".bundle.zip")
            bundle_path = manifest_path.with_name(out_name)
        else:
            bundle_path = manifest_path.with_suffix("").with_suffix(".bundle.zip")

    bundle_path.parent.mkdir(parents=True, exist_ok=True)

    files: List[Path] = [manifest_path]
    artifacts = data.get("artifacts") or {}

    def _collect(v: Any) -> None:
        if isinstance(v, dict) and "path" in v:
            try:
                p = Path(v["path"])
                files.append(p)
            except Exception:
                pass
        elif isinstance(v, list):
            for item in v:
                _collect(item)
        elif isinstance(v, dict):
            for vv in v.values():
                _collect(vv)

    _collect(artifacts)

    # de-dupe and keep existing files only
    uniq: List[Path] = []
    seen: set[str] = set()
    for p in files:
        rp = str(p)
        if rp in seen:
            continue
        seen.add(rp)
        if p.exists():
            uniq.append(p)

    cwd = Path.cwd().resolve()
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in uniq:
            try:
                arcname = str(p.resolve().relative_to(cwd))
            except Exception:
                arcname = p.name
            z.write(p, arcname=arcname)

    return bundle_path


# -----------------------------
# Exit snapshot
# -----------------------------
def capture_exit_snapshot(url: str, output_prefix: str, status: str) -> str:
    """
    Best-effort browser screenshot using Playwright (if available).

    Writes:
      <output_prefix>.exit.<status>.png
    Returns the PNG path as a string.

    Requires:
      pip install playwright
      playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Playwright is not installed. Install with: pip install playwright "
            "then run: playwright install chromium"
        ) from e

    out_png = Path(f"{output_prefix}.exit.{status}.png")
    out_png.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(out_png), full_page=True)
        browser.close()

    return str(out_png)


# -----------------------------
# CLI parsing helpers
# -----------------------------
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

    # -----------------------------
    # http-fuzz
    # -----------------------------
    http_parser = subparsers.add_parser(
        "http-fuzz",
        help="Run a simple HTTP fuzz / brute-force job",
    )

    # url/wordlist optional when using --profile; enforced in code
    http_parser.add_argument(
        "--url",
        required=False,
        default=None,
        help="Target URL. Use {payload} as a placeholder in the query or path.",
    )
    http_parser.add_argument("--method", default="GET", help="HTTP method (GET, POST, etc.). Default: GET")
    http_parser.add_argument(
        "--wordlist",
        required=False,
        default=None,
        help="Path to wordlist file (one payload per line).",
    )
    http_parser.add_argument("--concurrency", type=int, default=10, help="Number of concurrent requests. Default: 10")
    http_parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds. Default: 10")
    http_parser.add_argument("--output", type=str, default=None, help="Optional JSONL output path for results.")
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
        "--urlencode-payload",
        action="store_true",
        help="URL-encode payload when substituting into the URL placeholder (recommended for query/path fuzzing).",
    )
    http_parser.add_argument(
        "--header",
        action="append",
        default=None,
        help=(
            "Optional HTTP header, can be used multiple times. "
            'Example: --header "Authorization: Bearer TOKEN" --header "X-Env: dev"'
        ),
    )
    http_parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Optional profile name to load from profiles.toml (e.g. 'example-login-dev').",
    )

    # Safety controls
    http_parser.add_argument("--max-rps", type=float, default=5.0, help="Maximum requests per second (global). Default: 5.0")
    http_parser.add_argument(
        "--stop-on-error-rate",
        type=float,
        default=0.5,
        help="Stop if recent error fraction exceeds this (0–1). Default: 0.5",
    )
    http_parser.add_argument(
        "--error-window",
        type=int,
        default=50,
        help="Number of most-recent requests used to compute error rate. Default: 50",
    )
    http_parser.add_argument("--env", type=str, default="dev", choices=["dev", "stage", "prod"], help="Environment label (dev, stage, prod). Default: dev")
    http_parser.add_argument(
        "--i-understand-prod",
        action="store_true",
        help="Required when --env prod is used, to acknowledge live-target testing.",
    )
    http_parser.add_argument("--dry-run", action="store_true", help="Show what would be executed, but do not send any HTTP requests.")
    http_parser.add_argument("--mode", type=str, default="default", choices=["default", "recon", "auth-pressure"], help="Assessment mode: default | recon | auth-pressure")
    http_parser.add_argument(
        "--bundle",
        action="store_true",
        help="Create a <output>.bundle.zip from the manifest + listed artifacts (requires --output).",
    )

    # -----------------------------
    # http-flow
    # -----------------------------
    http_flow_parser = subparsers.add_parser(
        "http-flow",
        help="Run a multi-step HTTP flow defined in flows.toml",
    )
    http_flow_parser.add_argument("--flows-file", type=str, default="flows.toml", help="Path to flows TOML file (default: flows.toml).")
    http_flow_parser.add_argument("--flow", type=str, required=False, help="Flow name from flows.toml (e.g. example-login-sequence).")
    http_flow_parser.add_argument("--list", action="store_true", help="List available flows from flows.toml and exit.")
    http_flow_parser.add_argument("--lint", action="store_true", help="Validate flows TOML structure/types and exit (no network). Non-zero on errors.")
    http_flow_parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds. Default: 10")
    http_flow_parser.add_argument("--max-rps", type=float, default=2.0, help="Max requests per second across the flow. Default: 2.0")
    http_flow_parser.add_argument("--env", type=str, default="dev", choices=["dev", "stage", "prod"], help="Environment label for this flow. Default: dev")
    http_flow_parser.add_argument(
        "--i-understand-prod",
        action="store_true",
        help="Required when --env prod is used, to acknowledge live-target testing.",
    )
    http_flow_parser.add_argument("--dry-run", action="store_true", help="Show the flow steps without sending any HTTP requests.")
    http_flow_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional output prefix for logs. If set, writes <prefix>.jsonl and summaries/reports.",
    )
    http_flow_parser.add_argument(
        "--stop-on-fail",
        action="store_true",
        help="Stop the entire flow as soon as any step fails.",
    )
    http_flow_parser.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="Force the flow to continue through all steps even if some fail. Overrides per-step stop flags.",
    )
    http_flow_parser.add_argument("--vars-dump", action="store_true", help="After the flow finishes, print extracted variables.")
    http_flow_parser.add_argument("--save-body", action="store_true", help="When steps fail, write response bodies to disk.")
    http_flow_parser.add_argument("--mode", type=str, default="default", choices=["default", "recon", "auth-pressure"], help="Assessment mode: default | recon | auth-pressure")
    http_flow_parser.add_argument(
        "--var",
        action="append",
        default=None,
        help="Set a template variable for this flow, e.g. --var username=admin --var password=secret",
    )
    http_flow_parser.add_argument("--exit-snapshot", action="store_true", help="Capture a browser screenshot at flow exit (PASS or FAIL)")
    http_flow_parser.add_argument("--no-jsonl", action="store_true", help="Do not write <output>.jsonl")
    http_flow_parser.add_argument("--no-summary-md", action="store_true", help="Do not write <output>.summary.md")
    http_flow_parser.add_argument("--no-summary-json", action="store_true", help="Do not write <output>.summary.json")
    http_flow_parser.add_argument("--quiet", action="store_true", help="Suppress 'Wrote:' line (still prints errors)")
    http_flow_parser.add_argument(
        "--bundle",
        action="store_true",
        help="Create a <output>.bundle.zip from the manifest + listed artifacts (requires --output).",
    )

    return parser


# -----------------------------
# http-fuzz summarization + artifacts
# -----------------------------
def summarize_results(results) -> None:
    """
    Print a quick summary grouping by (status_code, content_length)
    and showing sample payloads.
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
            print(f"  * status={status_str}, size={size_str} bytes → {len(payloads)} payload(s): {payloads}")


def _percentile(values: List[float], pct: float) -> Optional[float]:
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
                ts = getattr(r, "timestamp", None)
                if hasattr(ts, "isoformat"):
                    ts = ts.isoformat()
                error_examples.append(
                    {
                        "payload": r.payload,
                        "status_code": status,
                        "error": getattr(r, "error", None),
                        "elapsed_ms": getattr(r, "elapsed_ms", None),
                        "timestamp": ts,
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
        "target": {"method": getattr(target, "method", None), "url": getattr(target, "url", None)},
        "env": None,
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
    status_codes = summary.get("status_counts", {}) or {}
    error_count = int(summary.get("error_count", 0) or 0)
    error_rate = float(summary.get("error_rate", 0.0) or 0.0)

    has_server_errors = any(
        (code not in (None, "None"))
        and isinstance(code, str)
        and code.isdigit()
        and int(code) >= 500
        for code in status_codes.keys()
    )

    passed = (error_count == 0) and (not has_server_errors) and (error_rate == 0.0)

    lines: List[str] = []
    lines.append("# ✅ CATE Run Passed" if passed else "# ❌ CATE Run Failed")
    lines.append("")
    lines.append("## Status Codes\n")
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    for code, count in sorted(status_codes.items(), key=lambda kv: kv[0]):
        lines.append(f"| {code} | {count} |")
    lines.append("")
    latency = summary.get("latency", {}) or {}
    lines.append("## Latency (ms)\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for key in ["count", "min_ms", "max_ms", "mean_ms", "p50_ms", "p90_ms", "p99_ms"]:
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
    env: Optional[str] = None,
    mode: Optional[str] = None,
) -> None:
    """
    Given the main JSONL output path, write:
      - <name>.summary.json
      - <name>.summary.md
    Then compute signals + reports, then write evidence manifest.
    """
    summary = build_run_summary(results, config)
    if env:
        summary["env"] = env
    if mode:
        summary["mode"] = mode

    json_path = output_path.with_suffix(".summary.json")
    md_path = output_path.with_suffix(".summary.md")

    try:
        json_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    except Exception as exc:
        print(_color(f"[CATE] Failed to write JSON summary: {exc}", _FG_YELLOW))

    try:
        md_text = render_markdown_summary(summary)
        md_path.write_text(md_text, encoding="utf-8")
    except Exception as exc:
        print(_color(f"[CATE] Failed to write Markdown summary: {exc}", _FG_YELLOW))

    # Signals + reports + manifest (best-effort)
    try:
        top_trigger = None
        ex = summary.get("error_examples") or []
        if ex:
            top_trigger = ex[0].get("payload")

        try:
            kind, w = validate_summary(summary)
            summary["kind"] = kind
            if w:
                print(_color(f"[CATE] Contract warnings (summary): {', '.join(w)}", _FG_YELLOW))
        except ContractError as ce:
            print(_color(f"[CATE] Contract error (summary): {ce}", _FG_YELLOW))

        from cate.contracts import build_and_validate_signals

        signals = build_and_validate_signals(summary, strict=False)
        signals["mode"] = summary.get("mode", "default")
        signals["top_trigger"] = top_trigger
        signals = finalize_signals(signals)

        try:
            w = validate_signals(signals)
            if w:
                print(_color(f"[CATE] Contract warnings (signals): {', '.join(w)}", _FG_YELLOW))
        except ContractError as ce:
            print(_color(f"[CATE] Contract error (signals): {ce}", _FG_YELLOW))

        signals_json_path = write_signals_json(signals, str(output_path))
        signals_md_path = write_signals_md(signals, str(output_path))

        try:
            report_path = write_report_md(
                output_prefix=str(output_path),
                env=env,
                kind=signals.get("kind"),
                summary_json_path=str(json_path),
                summary_md_path=str(md_path),
                signals_json_path=signals_json_path,
                signals_md_path=signals_md_path,
            )
            print(_color(f"[CATE] Report written to {report_path}", _FG_GREEN))
        except Exception as exc:
            print(_color(f"[CATE] Failed to write report: {exc}", _FG_YELLOW))

        try:
            from .reporting import write_report_html

            html_report_path = write_report_html(
                output_prefix=str(output_path),
                env=env,
                kind=signals.get("kind"),
                summary_json_path=str(json_path),
                summary_md_path=str(md_path),
                signals_json_path=signals_json_path,
                signals_md_path=signals_md_path,
                jsonl_path=str(output_path),
            )
            print(_color(f"[CATE] HTML report written to {html_report_path}", _FG_GREEN))
        except Exception as exc:
            print(_color(f"[CATE] Failed to write HTML report: {exc}", _FG_YELLOW))

        print(_color(f"[CATE] Signals written to {signals_json_path}", _FG_GREEN))
        print(_color(f"[CATE] Signals written to {signals_md_path}", _FG_GREEN))
        _print_signal_verdict(signals)

        # Evidence manifest (fuzz) — after all artifacts are present
        try:
            manifest_path = write_evidence_manifest(
                output_prefix=str(output_path),
                kind="http-fuzz",
                env=env,
                command=" ".join(sys.argv),
                version=__version__,
                extra={
                    "mode": summary.get("mode", "default"),
                    "target_url": (summary.get("target") or {}).get("url"),
                    "method": (summary.get("target") or {}).get("method"),
                    "concurrency": summary.get("concurrency"),
                    "max_rps": summary.get("max_rps"),
                    "total_payloads": summary.get("total_payloads"),
                    "error_count": summary.get("error_count"),
                },
            )
            print(_color(f"[CATE] Evidence manifest written to {manifest_path}", _FG_GREEN))
        except Exception as exc:
            print(_color(f"[CATE] Failed to write evidence manifest: {exc}", _FG_YELLOW))

    except Exception as exc:
        print(_color(f"[CATE] Failed to write signals: {exc}", _FG_YELLOW))


# -----------------------------
# Profiles → effective config
# -----------------------------
def build_effective_config(args) -> Dict[str, Any]:
    """
    Combine profile (if any) + CLI flags into a single config dict.
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
        urlencode_payload = profile_data.get("urlencode_payload", args.urlencode_payload)
        error_window = profile_data.get("error_window", args.error_window)

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
        urlencode_payload = args.urlencode_payload
        error_window = args.error_window

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
        "urlencode_payload": urlencode_payload,
        "error_window": error_window,
    }


# -----------------------------
# http-fuzz runner
# -----------------------------
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
    urlencode_payload: bool,
    error_window: int,
    mode: str,
    bundle: bool,
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

    # DRY RUN
    if dry_run:
        print(_color("[CATE] DRY RUN — no HTTP requests will be sent.", _FG_MAGENTA))
        print(_color(f"[CATE] Environment: {env}", _FG_CYAN))
        print(_color(f"[CATE] Target: {method.upper()} {url}", _FG_CYAN))
        print(_color(f"[CATE] Wordlist: {wordlist}", _FG_CYAN))
        print(_color(f"[CATE] Concurrency={concurrency}, max_rps={max_rps}, stop_on_error_rate={stop_on_error_rate}", _FG_CYAN))
        if headers:
            print(_color(f"[CATE] Headers: {headers}", _FG_CYAN))
        if body_template:
            print(_color(f"[CATE] Body template: {body_template}", _FG_CYAN))
        print(_color(f"[CATE] Placeholder: {placeholder}", _FG_CYAN))
        print(_color(f"[CATE] Mode: {mode}", _FG_CYAN))
        return 0

    print(_color(f"[CATE] Environment: {env}", _FG_CYAN))
    print(
        _color(
            f"[CATE] Config: method={method}, concurrency={concurrency}, max_rps={max_rps}, "
            f"stop_on_error_rate={stop_on_error_rate}, urlencode_payload={urlencode_payload}",
            _FG_CYAN,
        )
    )
    print(_color(f"[CATE] Mode: {mode}", _FG_CYAN))

    if body_template:
        print(_color(f"[CATE] Using body template: {body_template!r}", _FG_CYAN))
    if headers:
        print(_color(f"[CATE] Using headers: {headers!r}", _FG_CYAN))

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
        urlencode_payload=urlencode_payload,
        error_window=error_window,
    )

    async def _run() -> int:
        results = await run_job(config)

        output_path: Optional[Path] = None
        if output:
            output_path = Path(output)
            write_results_jsonl(output_path, results)

        total = len(results)
        errors = sum(1 for r in results if r.error or (r.status_code is not None and r.status_code >= 500))
        if errors:
            print(_color(f"[CATE] Completed {total} payloads ({errors} errors).", _FG_YELLOW))
        else:
            print(_color(f"[CATE] Completed {total} payloads (0 errors).", _FG_GREEN))

        if output_path is not None:
            print(_color(f"[CATE] Results written to {output_path}", _FG_GREEN))
            write_run_summaries(output_path, results, config, env=env, mode=mode)

            # Bundle: after manifest exists (manifest is written by write_run_summaries)
            if bundle:
                try:
                    manifest_path = output_path.with_suffix(".manifest.json")
                    bundle_path = bundle_from_manifest(manifest_path)
                    print(_color(f"[CATE] Bundle written to {bundle_path}", _FG_GREEN))
                except Exception as exc:
                    print(_color(f"[CATE] Failed to bundle artifacts: {exc}", _FG_YELLOW))

        summarize_results(results)
        return 0

    return asyncio.run(_run())


# -----------------------------
# Flow lint
# -----------------------------
def lint_flows(flows_path: Optional[Path]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    warnings: List[str] = []
    errors: List[str] = []

    try:
        flows = load_flows(flows_path)
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"[CATE] Failed to parse flows file: {exc}") from exc

    if not flows:
        warnings.append("No flows found.")

    for name, flow in flows.items():
        if not getattr(flow, "steps", None):
            errors.append(f"Flow '{name}' has no steps.")
            continue
        for idx, step in enumerate(flow.steps, 1):
            if not getattr(step, "name", None):
                errors.append(f"Flow '{name}' step {idx} is missing a name.")
            if not getattr(step, "method", None):
                errors.append(f"Flow '{name}' step {idx} is missing method.")
            if not getattr(step, "url", None):
                errors.append(f"Flow '{name}' step {idx} is missing url.")

    return flows, warnings, errors


# -----------------------------
# Flow logs (jsonl/summary/signals/reports)
# -----------------------------
def write_flow_logs(
    output_prefix: str,
    results: List[Dict[str, Any]],
    env: Optional[str] = None,
    initial_vars: Optional[Dict[str, Any]] = None,
    save_body: bool = False,
    write_jsonl: bool = True,
    write_summary_md: bool = True,
    write_summary_json: bool = True,
    mode: Optional[str] = None,
) -> List[Path]:
    """
    Write flow execution logs:

      - <output_prefix>.jsonl
      - <output_prefix>.summary.md
      - <output_prefix>.summary.json
      - signals + reports (via reporting module)
      - failing step bodies (optional)
    """
    written: List[Path] = []
    sj: Optional[str] = None
    smd: Optional[str] = None
    signals: Optional[Dict[str, Any]] = None

    out = Path(output_prefix)
    if out.suffix.lower() == ".jsonl":
        jsonl_path = out
    else:
        jsonl_path = Path(f"{output_prefix}.jsonl")

    summary_md_path = jsonl_path.with_suffix(".summary.md")
    summary_json_path = jsonl_path.with_suffix(".summary.json")

    for p, enabled in [
        (jsonl_path, write_jsonl),
        (summary_md_path, write_summary_md),
        (summary_json_path, write_summary_json),
    ]:
        if enabled:
            p.parent.mkdir(parents=True, exist_ok=True)

    # JSONL
    if write_jsonl:
        with jsonl_path.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        written.append(jsonl_path)

    total = len(results)
    failing = [r for r in results if not r.get("ok")]
    failures = len(failing)
    avg_ms = (sum(r.get("elapsed_ms", 0.0) for r in results) / total) if total > 0 else 0.0

    final_vars: Dict[str, Any] = {}
    for r in results:
        var_name = r.get("extracted_var")
        var_value = r.get("extracted_value")
        if var_name is not None and var_value is not None:
            final_vars[var_name] = var_value

    summary_obj: Dict[str, Any] = {
        "steps": total,
        "failures": failures,
        "avg_latency_ms": avg_ms,
        "final_vars": final_vars,
        "mode": mode or "default",
    }

    fail_samples: List[Dict[str, Any]] = []
    for r in results:
        if r.get("ok"):
            continue
        expected = None
        err = r.get("error")
        if isinstance(err, str):
            m = re.search(r"expected status(?: in)? (\[[^\]]+\])", err)
            if m:
                expected = m.group(1)

        fail_samples.append({"step": r.get("step"), "status": r.get("status_code"), "expected": expected, "error": err})
        if len(fail_samples) >= 3:
            break
    summary_obj["fail_samples"] = fail_samples

    # summary.json
    if write_summary_json:
        try:
            summary_json_path.write_text(json.dumps(summary_obj, indent=2, sort_keys=True), encoding="utf-8")
            written.append(summary_json_path)
        except Exception:
            pass

    # contract check summary
    try:
        kind, warnings = validate_summary(summary_obj)
        summary_obj["kind"] = kind
        if warnings:
            print(_color(f"[CATE] Contract warnings (summary): {', '.join(warnings)}", _FG_YELLOW))
    except ContractError as ce:
        print(_color(f"[CATE] Contract error (summary): {ce}", _FG_YELLOW))

    # signals
    try:
        from cate.contracts import build_and_validate_signals

        signals = build_and_validate_signals(summary_obj)
        signals["mode"] = summary_obj.get("mode", "default")
        signals = finalize_signals(signals)

        try:
            warnings = validate_signals(signals)
            if warnings:
                print(_color(f"[CATE] Contract warnings (signals): {', '.join(warnings)}", _FG_YELLOW))
        except ContractError as ce:
            print(_color(f"[CATE] Contract error (signals): {ce}", _FG_YELLOW))

        sj = write_signals_json(signals, str(Path(output_prefix)))
        smd = write_signals_md(signals, str(Path(output_prefix)))
        print(_color(f"[CATE] Signals written to {sj}", _FG_GREEN))
        print(_color(f"[CATE] Signals written to {smd}", _FG_GREEN))
        _print_signal_verdict(signals)

    except Exception as exc:
        print(_color(f"[CATE] Failed to write signals: {exc}", _FG_YELLOW))

    # summary.md + reports
    if write_summary_md:
        summary_md_path.write_text(render_flow_summary_md(results, env=env, initial_vars=initial_vars), encoding="utf-8")
        written.append(summary_md_path)

        # report + html report best-effort
        try:
            if sj and smd:
                report_path = write_report_md(
                    output_prefix=str(Path(output_prefix)),
                    env=env,
                    kind=(signals or {}).get("kind", "http-flow"),
                    summary_json_path=str(summary_json_path) if write_summary_json else None,
                    summary_md_path=str(summary_md_path) if write_summary_md else None,
                    signals_json_path=sj,
                    signals_md_path=smd,
                )
                print(_color(f"[CATE] Report written to {report_path}", _FG_GREEN))
                written.append(Path(report_path))

                try:
                    from .reporting import write_report_html

                    html_report_path = write_report_html(
                        output_prefix=str(Path(output_prefix)),
                        env=env,
                        kind=(signals or {}).get("kind", "http-flow"),
                        summary_json_path=str(summary_json_path) if write_summary_json else None,
                        summary_md_path=str(summary_md_path) if write_summary_md else None,
                        signals_json_path=sj,
                        signals_md_path=smd,
                        jsonl_path=str(jsonl_path) if write_jsonl else None,
                    )
                    print(_color(f"[CATE] HTML report written to {html_report_path}", _FG_GREEN))
                    written.append(Path(html_report_path))
                except Exception as exc:
                    print(_color(f"[CATE] Failed to write HTML report: {exc}", _FG_YELLOW))

        except Exception as exc:
            print(_color(f"[CATE] Failed to write report: {exc}", _FG_YELLOW))

    # failing bodies
    if save_body:
        for idx, r in enumerate(results, 1):
            if r.get("ok"):
                continue
            body = r.get("body")
            if not body:
                continue

            step_name = str(r.get("step", f"step{idx}"))
            safe_step = re.sub(r"[^A-Za-z0-9_-]+", "_", step_name)

            body_path = Path(f"{output_prefix}.step{idx}_{safe_step}.body.txt")
            html_capture = Path(f"{output_prefix}.step{idx}_{safe_step}.body.html")

            try:
                body_path.parent.mkdir(parents=True, exist_ok=True)
                body_text = body if isinstance(body, str) else str(body)
                body_path.write_text(body_text, encoding="utf-8")
                written.append(body_path)

                lower = body_text.lower()
                if "<html" in lower or "<!doctype html" in lower:
                    html_capture.write_text(body_text, encoding="utf-8")
                    written.append(html_capture)
            except Exception:
                pass

    return written


# -----------------------------
# main
# -----------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # http-fuzz
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
            urlencode_payload=cfg["urlencode_payload"],
            error_window=cfg["error_window"],
            mode=args.mode,
            bundle=bool(getattr(args, "bundle", False)),
        )

    # http-flow
    if args.command == "http-flow":
        flows_path = Path(args.flows_file) if getattr(args, "flows_file", None) else None

        if args.stop_on_fail and args.continue_on_fail:
            print(_color("[CATE] Cannot use --stop-on-fail and --continue-on-fail together.", _FG_RED))
            return 1

        if getattr(args, "lint", False):
            try:
                _flows, warnings, errors = lint_flows(flows_path)
            except FileNotFoundError as e:
                print(_color(f"[CATE] {e}", _FG_RED))
                return 1
            except ValueError as e:
                print(_color(str(e), _FG_RED))
                return 1

            for w in warnings:
                print(_color(f"[CATE] {w}", _FG_YELLOW))

            if errors:
                print(_color("[CATE] Flow lint failed:", _FG_RED))
                for err in errors:
                    print(_color(f"  - {err}", _FG_RED))
                return 1

            print(_color("[CATE] Flow lint passed (no issues found).", _FG_GREEN))
            return 0

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
                print(f"  - {name}: {desc}" if desc else f"  - {name}")
            return 0

        if not args.flow:
            print(_color("[CATE] --flow is required unless --list is used.", _FG_RED))
            return 1

        try:
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
            print(
                f"  {idx}. {step.name} -> {step.method} {step.url} "
                f"(capture_cookies={step.capture_cookies}, expect_status={step.expect_status})"
            )

        initial_vars: Dict[str, Any] = {}
        if getattr(args, "var", None):
            initial_vars = parse_vars(args.var)
            if initial_vars:
                print(_color(f"[CATE] Seeded flow vars: {initial_vars}", _FG_CYAN))

        # DRY RUN: show interpolated requests
        if args.dry_run:
            print(
                _color(
                    f"[CATE] DRY RUN — not executing flow in env={args.env} (timeout={args.timeout}s, max_rps={args.max_rps})",
                    _FG_MAGENTA,
                )
            )
            vars_map: Dict[str, Any] = dict(initial_vars)
            print(_color("[CATE] DRY RUN request preview:", _FG_CYAN))
            for idx, step in enumerate(flow.steps, 1):
                url_preview = _apply_template_functions(step.url, vars_map)
                body_preview = _apply_template_functions(step.body_template, vars_map) if step.body_template is not None else None
                headers_preview = {k: _apply_template_functions(v, vars_map) for k, v in (step.headers or {}).items()}

                print(f"  Step {idx}: {step.name}")
                print(f"    {step.method} {url_preview}")
                if body_preview is not None:
                    print(f"    Body: {body_preview}")
                if headers_preview:
                    print(f"    Headers: {headers_preview}")
            return 0

        # Safety: block prod unless explicitly acknowledged
        if args.env == "prod" and not args.i_understand_prod:
            print(_color("[CATE] Refusing to run flow against env=prod without --i-understand-prod flag. Aborting.", _FG_RED))
            return 1

        print(
            _color(
                f"[CATE] Executing flow in env={args.env} (timeout={args.timeout}s, max_rps={args.max_rps})…",
                _FG_CYAN,
            )
        )

        stop_on_first_failure = bool(args.stop_on_fail)
        ignore_step_stop_flags = bool(args.continue_on_fail)

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
            line = f"{r['step']}: {r['method']} {r['url']} → status={status}, {r['elapsed_ms']:.1f} ms, {r['bytes']} bytes"
            if r["error"]:
                line += f" — {r['error']}"
            print(_color(f"[{status_label}] {line}", color))
            if not r["ok"]:
                failures += 1

        if args.vars_dump:
            vars_map: Dict[str, Any] = {}
            for r in results:
                vn = r.get("extracted_var")
                vv = r.get("extracted_value")
                if vn is not None and vv is not None:
                    vars_map[vn] = vv
            if not vars_map:
                print(_color("[CATE] No extracted variables to dump.", _FG_YELLOW))
            else:
                print(_color("[CATE] Extracted variables:", _FG_CYAN))
                for k, v in vars_map.items():
                    print(f"  - {k} = {v!r}")

        # write logs/summaries/signals/reports
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
                mode=args.mode,
            )

            if not getattr(args, "quiet", False):
                if written_paths:
                    print(_color(f"[CATE] Wrote: {', '.join(str(p) for p in written_paths)}", _FG_CYAN))
                else:
                    print(_color("[CATE] No output files written (all outputs disabled).", _FG_YELLOW))

        # exit snapshot (optional)
        if args.exit_snapshot and args.output:
            exit_status = "fail" if failures else "pass"

            exit_url = None
            if results:
                if failures:
                    for rr in results:
                        if not rr.get("ok"):
                            exit_url = rr.get("url")
                            break
                if exit_url is None:
                    exit_url = results[-1].get("url")

            if exit_url:
                try:
                    png_path = capture_exit_snapshot(exit_url, args.output, exit_status)
                    print(_color(f"[CATE] Exit snapshot written to {png_path}", _FG_GREEN))
                except Exception as exc:
                    print(_color(f"[CATE] Exit snapshot failed: {exc}", _FG_YELLOW))
            else:
                print(_color("[CATE] Exit snapshot skipped: no exit URL found.", _FG_YELLOW))

        # evidence manifest (flow) — after logs + optional snapshot
        if args.output:
            try:
                manifest_path = write_evidence_manifest(
                    output_prefix=args.output,
                    kind="http-flow",
                    env=args.env,
                    command=" ".join(sys.argv),
                    version=__version__,
                    extra={
                        "flow": flow.name,
                        "flows_file": args.flows_file,
                        "mode": args.mode,
                        "exit_snapshot": bool(args.exit_snapshot),
                        "failures": failures,
                    },
                )
                print(_color(f"[CATE] Evidence manifest written to {manifest_path}", _FG_GREEN))
            except Exception as exc:
                print(_color(f"[CATE] Failed to write evidence manifest: {exc}", _FG_YELLOW))

        # bundle (flow) — after manifest exists
        if args.output and getattr(args, "bundle", False):
            try:
                manifest_path = Path(f"{args.output}.manifest.json")
                bundle_path = bundle_from_manifest(manifest_path)
                print(_color(f"[CATE] Bundle written to {bundle_path}", _FG_GREEN))
            except Exception as exc:
                print(_color(f"[CATE] Failed to bundle artifacts: {exc}", _FG_YELLOW))

        if failures:
            print(_color(f"[CATE] Flow completed with {failures} failing step(s).", _FG_RED))
            return 1

        print(_color("[CATE] Flow completed successfully.", _FG_GREEN))
        return 0

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
