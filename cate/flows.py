from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio
import re
import time

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

    headers: Optional[Dict[str, str]] = None

    # Assertions (v0.3)
    max_latency_ms: Optional[float] = None
    body_must_contain: Optional[str] = None
    body_must_not_contain: Optional[str] = None
    stop_on_fail: bool = False

    # Variables / extractors (v0.3.1)
    extract_regex: Optional[str] = None
    store_as: Optional[str] = None
    require_extracted: bool = False


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

            raw_headers = raw.get("headers")
            headers: Optional[Dict[str, str]] = None
            if isinstance(raw_headers, dict):
                headers = {str(k): str(v) for k, v in raw_headers.items()}

            step = FlowStep(
                name=step_name,
                method=method,
                url=url,
                body_template=raw.get("body_template"),
                capture_cookies=bool(raw.get("capture_cookies", False)),
                expect_status=raw.get("expect_status"),
                headers=headers, 

                max_latency_ms=raw.get("max_latency_ms"),
                body_must_contain=raw.get("body_must_contain"),
                body_must_not_contain=raw.get("body_must_not_contain"),
                stop_on_fail=bool(raw.get("stop_on_fail", False)),

                extract_regex=raw.get("extract_regex"),
                store_as=raw.get("store_as"),
                require_extracted=bool(raw.get("require_extracted", False)),
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


async def _run_flow_async(
    flow: Flow,
    timeout: float = 10.0,
    max_rps: float = 2.0,
    stop_on_first_failure: bool = False,
    ignore_step_stop_flags: bool = False,
) -> List[Dict[str, Any]]:


    """
    Execute a Flow with a shared cookie jar and simple variable store.

    Returns a list of dicts, one per step, shaped roughly like:

        {
            "step": "login",
            "method": "POST",
            "url": "https://delphonix.com/login.php",
            "status_code": 200,
            "ok": True,
            "elapsed_ms": 347.2,
            "bytes": 2512,
            "error": None,
            "assertions": {
                "status_ok": True,
                "latency_ok": True,
                "body_contains_ok": True,
                "body_not_contains_ok": True,
                "extracted_ok": True,
            },
            "extracted_var": "marker",
            "extracted_value": "About",
        }
    """
    state: Dict[str, Any] = {"cookies": httpx.Cookies(), "vars": {}}
    results: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout, cookies=state["cookies"]) as client:
        last_start = 0.0

        for step in flow.steps:
            # RPS governor
            if max_rps > 0 and last_start > 0:
                min_interval = 1.0 / max_rps
                now = time.perf_counter()
                delta = now - last_start
                if delta < min_interval:
                    await asyncio.sleep(min_interval - delta)

            method = step.method
            url_template = step.url
            body_template = step.body_template

            # Variable interpolation helper
            def interpolate(template: Optional[str]) -> Optional[str]:
                if template is None:
                    return None
                try:
                    return template.format(**state["vars"])
                except KeyError:
                    # Missing vars – just return the raw template
                    return template

            url = interpolate(url_template) or url_template
            data = interpolate(body_template)

            # per-step headers with interpolation
            headers: Optional[Dict[str, str]] = None
            if step.headers:
                headers = {
                    key: interpolate(value) or value
                    for key, value in step.headers.items()
                }


            started = time.perf_counter()
            try:
                resp = await client.request(
                    method,
                    url,
                    data=data,
                    headers=headers,
                )

                elapsed_ms = (time.perf_counter() - started) * 1000.0
                status = resp.status_code
                size = len(resp.content)
                body_text: Optional[str] = None  # lazy

                ok = True
                error_msg_parts: List[str] = []
                assertions: Dict[str, bool] = {}

                # Status assertion
                if step.expect_status is not None:
                    if status == step.expect_status:
                        assertions["status_ok"] = True
                    else:
                        assertions["status_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            f"expected status {step.expect_status}, got {status}"
                        )

                # Latency assertion
                if step.max_latency_ms is not None:
                    if elapsed_ms <= step.max_latency_ms:
                        assertions["latency_ok"] = True
                    else:
                        assertions["latency_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            f"latency {elapsed_ms:.1f} ms > max {step.max_latency_ms:.1f} ms"
                        )

                # Body content assertions (load body lazily)
                if step.body_must_contain is not None or step.body_must_not_contain is not None:
                    body_text = resp.text

                if step.body_must_contain is not None:
                    if step.body_must_contain in (body_text or ""):
                        assertions["body_contains_ok"] = True
                    else:
                        assertions["body_contains_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            f"body does not contain {step.body_must_contain!r}"
                        )

                if step.body_must_not_contain is not None:
                    if step.body_must_not_contain in (body_text or ""):
                        assertions["body_not_contains_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            f"body contains forbidden {step.body_must_not_contain!r}"
                        )
                    else:
                        assertions["body_not_contains_ok"] = True

                # Extractor / variable assertion
                extracted_var = None
                extracted_value = None
                if step.extract_regex and step.store_as:
                    if body_text is None:
                        body_text = resp.text
                    m = re.search(step.extract_regex, body_text, flags=re.DOTALL)
                    if m:
                        # use first capturing group if present, else the whole match
                        extracted_value = m.group(1) if m.groups() else m.group(0)
                        extracted_var = step.store_as
                        state["vars"][step.store_as] = extracted_value
                        assertions["extracted_ok"] = True
                    else:
                        assertions["extracted_ok"] = False
                        if step.require_extracted:
                            ok = False
                            error_msg_parts.append(
                                f"failed to extract '{step.store_as}' with regex {step.extract_regex!r}"
                            )

                error_msg = "; ".join(error_msg_parts) if error_msg_parts else None

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
                        "assertions": assertions,
                        "extracted_var": extracted_var,
                        "extracted_value": extracted_value,
                        "headers": headers or {},
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
                        "assertions": {},
                        "extracted_var": None,
                        "extracted_value": None,
                        "headers": headers or {},
                    }
                )

            last_start = time.perf_counter()

            # Early stop if this step failed and either:
            #  - global stop_on_first_failure is True, or
            #  - this step's stop_on_fail is True (unless globally ignored)
            if not results[-1]["ok"]:
                step_wants_stop = step.stop_on_fail and not ignore_step_stop_flags
                if stop_on_first_failure or step_wants_stop:
                    break



    return results


def run_flow(
    flow: Flow,
    timeout: float = 10.0,
    max_rps: float = 2.0,
    stop_on_first_failure: bool = False,
    ignore_step_stop_flags: bool = False,
) -> List[Dict[str, Any]]:
    """
    Synchronous wrapper around _run_flow_async.
    """
    return asyncio.run(
        _run_flow_async(
            flow,
            timeout=timeout,
            max_rps=max_rps,
            stop_on_first_failure=stop_on_first_failure,
            ignore_step_stop_flags=ignore_step_stop_flags,
        )
    )

