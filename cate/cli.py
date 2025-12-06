# cate/cli.py

# bash script to run:
# cate http-fuzz \
#   --url "https://example.com/login?user=admin&pass={payload}" \
#   --wordlist /path/to/wordlist.txt \
#   --concurrency 20 \
#   --output ./logs/example-login.jsonl


from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from .engine import run_job
from .logging_utils import write_results_jsonl
from .models import JobConfig, Target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cate",
        description="Calypso Automated Testing Engine",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    http_parser = subparsers.add_parser(
        "http-fuzz", help="Run a simple HTTP fuzz / brute-force job"
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

    return parser


def run_http_fuzz(
    url: str,
    method: str,
    wordlist: str,
    concurrency: int,
    timeout: float,
    output: Optional[str],
    placeholder: str,
) -> int:
    target = Target(url=url, method=method)
    config = JobConfig(
        target=target,
        wordlist_path=wordlist,
        concurrency=concurrency,
        timeout_seconds=timeout,
        output_path=output,
        placeholder=placeholder,
    )

    async def _run() -> int:
        results = await run_job(config)
        if output:
            write_results_jsonl(Path(output), results)
        # Simple console summary for v0.1
        total = len(results)
        errors = sum(1 for r in results if r.error)
        print(f"[CATE] Completed {total} payloads ({errors} errors).")
        if output:
            print(f"[CATE] Results written to {output}")
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
        )

    parser.error("Unknown command")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
