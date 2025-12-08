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

    # Basic status assertion (already existed)
    expect_status: Optional[int] = None

    # NEW: lightweight per-step assertions
    max_latency_ms: Optional[float] = None          # e.g. 500.0
    body_must_contain: Optional[str] = None         # e.g. "Dashboard"
    body_must_not_contain: Optional[str] = None     # e.g. "Traceback"


@dataclass
class Flow:
    name: str
    description: str
    steps: List[FlowStep]
    stop_on_fail: bool = False    # Aborts flow when a step fails


@dataclass
class FlowStepResult:
    step: FlowStep
    status_code: int | None
    latency_ms: float
    bytes: int | None
    ok: bool
    error: str | None = None


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
        max_latency_ms = 500.0
        body_must_contain = "Dashboard"
        body_must_not_contain = "Traceback"

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

                # NEW: optional assertion fields
                max_latency_ms=raw.get("max_latency_ms"),
                body_must_contain=raw.get("body_must_contain"),
                body_must_not_contain=raw.get("body_must_not_contain"),
            )
            steps.append(step)


        if steps:
            flows[flow_name] = Flow(
                name=flow_name,
                description=description,
                steps=steps,
                stop_on_fail=bool(cfg.get("stop_on_fail", False)),
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

            # v0.3.1: no per-step fuzz payload yet; use body_template as-is
            data: Optional[str] = step.body_template

            started = time.perf_counter()
            try:
                resp = await client.request(method, url, data=data)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                status = resp.status_code
                size = len(resp.content) if resp.content is not None else 0

                # --- assertion engine for this step ---
                assertion_errors: list[str] = []

                # 1) Status code
                if step.expect_status is not None and status != step.expect_status:
                    assertion_errors.append(
                        f"expected status {step.expect_status}, got {status}"
                    )

                # 2) Latency
                if step.max_latency_ms is not None and elapsed_ms > step.max_latency_ms:
                    assertion_errors.append(
                        f"latency {elapsed_ms:.1f} ms > max {step.max_latency_ms:.1f} ms"
                    )

                # 3) Body text checks
                text = ""
                try:
                    text = resp.text or ""
                except Exception:
                    # if decode fails, treat as empty for body checks
                    text = ""

                if step.body_must_contain:
                    if step.body_must_contain not in text:
                        assertion_errors.append(
                            f"body does not contain {step.body_must_contain!r}"
                        )

                if step.body_must_not_contain:
                    if step.body_must_not_contain in text:
                        assertion_errors.append(
                            f"body contains forbidden substring {step.body_must_not_contain!r}"
                        )

                ok = not assertion_errors
                error_msg: Optional[str] = (
                    "; ".join(assertion_errors) if assertion_errors else None
                )
                # --- end assertion engine ---

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

                if not ok and flow.stop_on_fail:
                    print(
                        _color(
                            f"[CATE] stop_on_fail=true — aborting flow after failing step '{step.name}'.",
                            _FG_YELLOW,
                        )
                    )
                    break

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
                        "error": f"request error: {exc}",
                    }
                )

                if flow.stop_on_fail:
                    print(
                        _color(
                            f"[CATE] stop_on_fail=true — aborting flow after exception in step '{step.name}'.",
                            _FG_YELLOW,
                        )
                    )
                    break

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
