from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Optional, Tuple, Dict, List

from .engine import run_job
from .logging_utils import write_results_jsonl
from .models import JobConfig, Target

def parse_headers(header_list: Optional[list[str]]) -> dict[str, str]:
    """
    Parse repeated --header 'Key: Value' into a dict.
    Ignores malformed entries.
    """
    headers: dict[str, str] = {}
    if not header_list:
        return headers

    for raw in header_list:
        # Split on the first colon only
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

    http_parser.add_argument(
        "--url",
        required=True,
        help="Target URL. Use {payload} as a placeholder in the query or path.",
    )
    http_parser.add_argument(
        "--method",
        default="GET",
        help="HTTP method (GET, POST, etc.). Default: GET",
    )
    http_parser.add_argument(
        "--wordlist",
        required=True,
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
        "--header",
        action="append",
        default=None,
        help=(
            "Optional HTTP header, can be used multiple times. "
            'Example: --header "Authorization: Bearer TOKEN" '
            '--header "X-Env: dev"'
        ),
    )


    # NEW: body template
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

    # ---- SAFETY CONTROLS ----
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

    # Sort by count descending
    sorted_groups = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)

    for (status_code, content_length), payloads in sorted_groups:
        count = len(payloads)
        status_str = "None" if status_code is None else str(status_code)
        size_str = "None" if content_length is None else str(content_length)

        # Show up to 5 sample payloads
        samples = payloads[:5]
        sample_str = ", ".join(samples)
        more = "" if count <= 5 else f" (+{count - 5} more)"

        print(
            f"  - status={status_str}, size={size_str} bytes: "
            f"{count} payload(s). Samples: [{sample_str}]{more}"
        )

    # Highlight potential outliers (small groups)
    print("\n[CATE] Potential outliers (rare response shapes):")
    for (status_code, content_length), payloads in sorted_groups:
        if len(payloads) <= 3:
            status_str = "None" if status_code is None else str(status_code)
            size_str = "None" if content_length is None else str(content_length)
            print(
                f"  * status={status_str}, size={size_str} bytes → "
                f"{len(payloads)} payload(s): {payloads}"
            )


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
    headers: dict[str, str],
) -> int:
    if env == "prod" and not i_understand_prod:
        print(
            "[CATE] Refusing to run against env=prod without "
            "--i-understand-prod flag. Aborting."
        )
        return 1

    print(f"[CATE] Environment: {env}")
    print(
        f"[CATE] Config: method={method}, concurrency={concurrency}, "
        f"max_rps={max_rps}, stop_on_error_rate={stop_on_error_rate}"
    )
    if body_template:
        print(f"[CATE] Using body template: {body_template!r}")
    if headers:
        print(f"[CATE] Using headers: {headers!r}")

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
        if output:
            write_results_jsonl(Path(output), results)

        total = len(results)
        errors = sum(
            1
            for r in results
            if r.error or (r.status_code is not None and r.status_code >= 500)
        )
        print(f"[CATE] Completed {total} payloads ({errors} errors).")
        if output:
            print(f"[CATE] Results written to {output}")

        summarize_results(results)
        return 0

    return asyncio.run(_run())


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "http-fuzz":
        return run_http_fuzz(
            url=args.url,
            method=args.method,
            wordlist=args.wordlist,
            concurrency=args.concurrency,
            timeout=args.timeout,
            output=args.output,
            placeholder=args.placeholder,
            body_template=args.body_template,
            max_rps=args.max_rps,
            stop_on_error_rate=args.stop_on_error_rate,
            env=args.env,
            i_understand_prod=args.i_understand_prod,
            headers=parse_headers(args.header),
        )

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
