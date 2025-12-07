from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any
from statistics import mean
from datetime import datetime
import sys

from .engine import run_job
from .logging_utils import write_results_jsonl
from .models import JobConfig, Target
from .profiles import load_profile, ProfileNotFound
from .flows import load_flow, run_flow, FlowNotFound

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cate",
        description="Calypso Automated Testing Engine",
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

    # NEW: profile name
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

    flow_parser = subparsers.add_parser(
        "http-flow",
        help="Run a stateful HTTP flow (login -> follow-up calls, etc.)",
    )

    flow_parser.add_argument(
        "--flow",
        required=True,
        help="Name of the flow to run (as defined in flows.toml).",
    )
    flow_parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds. Default: 10",
    )
    flow_parser.add_argument(
        "--max-rps",
        type=float,
        default=2.0,
        help="Max requests per second across the flow. Default: 2.0",
    )
    flow_parser.add_argument(
        "--env",
        type=str,
        default="dev",
        choices=["dev", "stage", "prod"],
        help="Environment label for this flow. Default: dev",
    )
    flow_parser.add_argument(
        "--i-understand-prod",
        action="store_true",
        help="Required when --env prod is used, to acknowledge live-target testing.",
    )
    flow_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the flow steps without sending any HTTP requests.",
    )

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
    now = datetime.utcnow().isoformat() + "Z"

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
    Render a human-friendly Markdown report from the summary dict.
    """
    target = summary.get("target", {})
    latency = summary.get("latency") or {}
    status_counts = summary.get("status_counts", {})

    lines: List[str] = []

    lines.append("# CATE Run Summary")
    lines.append("")
    lines.append(f"_Generated_: `{summary.get('generated_at', '')}`")
    lines.append("")

    lines.append("## Target")
    lines.append("")
    lines.append(f"- **Method**: `{target.get('method')}`")
    lines.append(f"- **URL**: `{target.get('url')}`")
    lines.append(f"- **Env**: `{summary.get('env')}`")
    lines.append(f"- **Wordlist**: `{summary.get('wordlist')}`")
    lines.append(f"- **Concurrency**: `{summary.get('concurrency')}`")
    lines.append(f"- **Timeout (s)**: `{summary.get('timeout_seconds')}`")
    lines.append(f"- **max_rps**: `{summary.get('max_rps')}`")
    lines.append(f"- **stop_on_error_rate**: `{summary.get('stop_on_error_rate')}`")
    lines.append("")

    lines.append("## Results")
    lines.append("")
    lines.append(f"- **Total payloads**: `{summary.get('total_payloads')}`")
    lines.append(f"- **Error count**: `{summary.get('error_count')}`")
    if summary.get("total_payloads"):
        lines.append(f"- **Error rate**: `{summary.get('error_rate'):.3f}`")
    else:
        lines.append("- **Error rate**: `n/a`")
    lines.append("")

    lines.append("### Status codes")
    lines.append("")
    if status_counts:
        lines.append("| Status | Count |")
        lines.append("|--------|-------|")
        for code, count in sorted(status_counts.items(), key=lambda kv: kv[0]):
            lines.append(f"| `{code}` | `{count}` |")
    else:
        lines.append("_No responses recorded._")
    lines.append("")

    lines.append("### Latency (ms)")
    lines.append("")
    if latency:
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        for key in ["count", "min_ms", "max_ms", "mean_ms", "p50_ms", "p90_ms", "p99_ms"]:
            value = latency.get(key)
            if value is None:
                continue
            lines.append(f"| `{key}` | `{value:.2f}` |")
    else:
        lines.append("_No latency data available._")
    lines.append("")

    errors = summary.get("error_examples") or []
    lines.append("### Sample errors / anomalies")
    lines.append("")
    if not errors:
        lines.append("_No error examples captured (nice!)._")
    else:
        for idx, err in enumerate(errors, 1):
            lines.append(f"#### Error {idx}")
            lines.append("")
            lines.append(f"- **Payload**: `{err.get('payload')}`")
            lines.append(f"- **Status**: `{err.get('status_code')}`")
            lines.append(f"- **Elapsed ms**: `{err.get('elapsed_ms')}`")
            lines.append(f"- **Error**: `{err.get('error')}`")
            ts = err.get("timestamp")
            if ts:
                lines.append(f"- **Timestamp**: `{ts}`")
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

    print(
        _color(
            f"[CATE] Summary written to {json_path} and {md_path}",
            _FG_GREEN,
        )
    )

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
        try:
            flow = load_flow(args.flow)
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

        # DRY RUN: just show what *would* happen
        if args.dry_run:
            print(_color("[CATE] DRY RUN — not executing flow (v0.3.0).", _FG_MAGENTA))
            return 0

        # REAL EXECUTION: run the flow with a shared HTTP client
        print(
            _color(
                "[CATE] Executing flow (v0.3.0 stateful HTTP run)…",
                _FG_CYAN,
            )
        )

        results = run_flow(
            flow,
            timeout=args.timeout if hasattr(args, "timeout") else 10.0,
            max_rps=args.max_rps if hasattr(args, "max_rps") else 2.0,
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



    parser.error("Unknown command")
    return 1



if __name__ == "__main__":
    raise SystemExit(main())
