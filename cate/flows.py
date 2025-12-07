from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import tomllib


class FlowNotFound(Exception):
    """Raised when a named flow cannot be found."""
    pass


@dataclass
class FlowStep:
    name: str
    method: str
    url: str
    body_template: Optional[str] = None
    capture_cookies: bool = False
    expect_status: Optional[int] = None


@dataclass
class Flow:
    name: str
    description: str
    steps: List[FlowStep]


def load_flows(path: Path | None = None) -> Dict[str, Flow]:
    """
    Load all flows from a TOML file.

    Expected shape in flows.toml:

        [flows.my-flow]
        description = "..."
        steps = ["login", "dashboard"]

        [flows.my-flow.login]
        method = "POST"
        url = "https://example.com/login"
        body_template = "user=admin&pass={password}"
        capture_cookies = true

        [flows.my-flow.dashboard]
        method = "GET"
        url = "https://example.com/dashboard"
        expect_status = 200
    """
    if path is None:
        path = Path("flows.toml")

    if not path.exists():
        raise FileNotFoundError(f"No flows file found at {path!s}")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    flows_section = data.get("flows", {})
    if not isinstance(flows_section, dict):
        raise ValueError("flows.toml must contain a [flows] table.")

    flows: Dict[str, Flow] = {}

    for flow_name, cfg in flows_section.items():
        if not isinstance(cfg, dict):
            continue

        description = cfg.get("description", "")
        steps_order = cfg.get("steps", [])
        if not isinstance(steps_order, list) or not steps_order:
            # Require explicit step order
            continue

        step_defs: Dict[str, Any] = {}
        # child tables appear as nested dicts in cfg
        for key, value in cfg.items():
            # heuristically treat nested dicts as step definitions
            if isinstance(value, dict) and key not in ("steps", "description"):
                step_defs[key] = value

        steps: List[FlowStep] = []
        for step_name in steps_order:
            raw = step_defs.get(step_name)
            if not isinstance(raw, dict):
                # skip unknown / malformed step
                continue
            method = str(raw.get("method", "GET")).upper()
            url = str(raw.get("url", ""))
            if not url:
                continue

            step = FlowStep(
                name=step_name,
                method=method,
                url=url,
                body_template=raw.get("body_template"),
                capture_cookies=bool(raw.get("capture_cookies", False)),
                expect_status=raw.get("expect_status"),
            )
            steps.append(step)

        if steps:
            flows[flow_name] = Flow(
                name=flow_name,
                description=description,
                steps=steps,
            )

    return flows


def load_flow(name: str, path: Path | None = None) -> Flow:
    flows = load_flows(path)
    if name not in flows:
        raise FlowNotFound(f"Flow '{name}' not found in flows.toml")
    return flows[name]


# ---------------------------------------------------------------------------
# v0.3: Flow execution helpers
# ---------------------------------------------------------------------------

async def run_flow_async(
    flow: Flow,
    *,
    timeout: float = 10.0,
    max_rps: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Execute a Flow step-by-step with a shared HTTP client (cookies are
    automatically carried across requests by httpx).

    Returns a list of dicts with per-step results:

        {
            "step": "login",
            "method": "POST",
            "url": "https://…",
            "status_code": 200,
            "ok": True/False,
            "elapsed_ms": 123.4,
            "bytes": 1024,
            "error": None or str,
        }
    """
    results: List[Dict[str, Any]] = []

    timeout_cfg = httpx.Timeout(timeout)
    async with httpx.AsyncClient(timeout=timeout_cfg, follow_redirects=True) as client:
        last_start: Optional[float] = None

        for step in flow.steps:
            method = step.method.upper()
            url = step.url

            # Simple global RPS throttle between steps
            if last_start is not None and max_rps > 0:
                min_interval = 1.0 / max_rps
                elapsed = time.perf_counter() - last_start
                sleep_for = min_interval - elapsed
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

            # v0.3: no per-step fuzz payload yet; use body_template as-is
            data: Optional[str] = step.body_template

            started = time.perf_counter()
            try:
                resp = await client.request(method, url, data=data)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                status = resp.status_code
                size = len(resp.content)

                ok = True
                error_msg: Optional[str] = None
                if step.expect_status is not None and status != step.expect_status:
                    ok = False
                    error_msg = f"expected {step.expect_status}, got {status}"

                results.append(
                    {
                        "step": step.name,
                        "method": method,
                        "url": url,
                        "status_code": status,
                        "ok": ok,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "bytes": size,
                        "error": error_msg,
                    }
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                results.append(
                    {
                        "step": step.name,
                        "method": method,
                        "url": url,
                        "status_code": None,
                        "ok": False,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "bytes": 0,
                        "error": str(exc),
                    }
                )

            # httpx client keeps cookies internally; we just track timing
            last_start = time.perf_counter()

    return results


def run_flow(
    flow: Flow,
    *,
    timeout: float = 10.0,
    max_rps: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Synchronous helper so callers (e.g. cli.py) can just do:

        results = run_flow(flow, timeout=10.0, max_rps=2.0)
    """
    return asyncio.run(run_flow_async(flow, timeout=timeout, max_rps=max_rps))
