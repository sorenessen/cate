from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import asyncio
import re
import time
import urllib.parse

import httpx
import tomllib
from urllib.parse import quote_plus

_TEMPLATE_RE = re.compile(r"{([^}]+)}")



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


# def _load_flows_file(path: Path, visited: Optional[Set[Path]] = None) -> Dict[str, Any]:
#     """
#     Recursive loader for flows TOML with a simple `include` mechanism.

#     Each file can optionally declare:

#         include = ["relative/path1.toml", "relative/path2.toml"]

#     All [flows.*] tables from included files are merged, with *later* files
#     overriding earlier ones on name collisions.
#     """
#     if visited is None:
#         visited = set()

#     path = path.resolve()
#     if path in visited:
#         # Prevent include cycles
#         return {"flows": {}}

#     visited.add(path)

#     if not path.exists():
#         raise FileNotFoundError(f"No flows file found at {path!s}")

#     text = path.read_text(encoding="utf-8")
#     data = tomllib.loads(text)

#     combined: Dict[str, Any] = {"flows": {}}

#     # 1) Process includes first (so this file can override them if desired)
#     includes = data.get("include", [])
#     if isinstance(includes, str):
#         includes = [includes]

#     if isinstance(includes, list):
#         for inc in includes:
#             inc_path = (path.parent / inc).resolve()
#             inc_data = _load_flows_file(inc_path, visited)
#             inc_flows = inc_data.get("flows", {})
#             if isinstance(inc_flows, dict):
#                 combined["flows"].update(inc_flows)

#     # 2) Merge this file's own [flows.*] tables
#     flows_section = data.get("flows", {})
#     if isinstance(flows_section, dict):
#         combined["flows"].update(flows_section)

#     return combined


# def load_flows(path: Path | None = None) -> Dict[str, Flow]:
#     """
#     Load all flows from a TOML file, with support for `include`.

#     Expected shape in flows.toml:

#         include = ["flows/demo-flows.toml"]

#         [flows.my-flow]
#         description = "..."
#         steps = ["login", "dashboard"]

#         [flows.my-flow.login]
#         method = "POST"
#         url = "https://example.com/login"
#         body_template = "user=admin&pass={password}"
#         capture_cookies = true

#         [flows.my-flow.dashboard]
#         method = "GET"
#         url = "https://example.com/dashboard"
#         expect_status = 200
#     """
#     if path is None:
#         path = Path("flows.toml")
#     else:
#         path = Path(path)

#     data = _load_flows_file(path)

#     flows_section = data.get("flows", {})
#     if not isinstance(flows_section, dict):
#         raise ValueError("flows.toml (and included files) must contain a [flows] table.")

#     flows: Dict[str, Flow] = {}

#     for flow_name, cfg in flows_section.items():
#         if not isinstance(cfg, dict):
#             continue

#         description = cfg.get("description", "")
#         steps_order = cfg.get("steps", [])
#         if not isinstance(steps_order, list) or not steps_order:
#             # Require explicit step order
#             continue

#         # Gather step definitions from nested tables
#         step_defs: Dict[str, Any] = {}
#         for key, value in cfg.items():
#             if isinstance(value, dict) and key not in ("steps", "description"):
#                 step_defs[key] = value

#         steps: List[FlowStep] = []
#         for step_name in steps_order:
#             raw = step_defs.get(step_name)
#             if not isinstance(raw, dict):
#                 # skip unknown / malformed step
#                 continue
#             method = str(raw.get("method", "GET")).upper()
#             url = str(raw.get("url", ""))
#             if not url:
#                 continue

#             raw_headers = raw.get("headers")
#             headers: Optional[Dict[str, str]] = None
#             if isinstance(raw_headers, dict):
#                 headers = {str(k): str(v) for k, v in raw_headers.items()}

#             step = FlowStep(
#                 name=step_name,
#                 method=method,
#                 url=url,
#                 body_template=raw.get("body_template"),
#                 capture_cookies=bool(raw.get("capture_cookies", False)),
#                 expect_status=raw.get("expect_status"),
#                 headers=headers,
#                 max_latency_ms=raw.get("max_latency_ms"),
#                 body_must_contain=raw.get("body_must_contain"),
#                 body_must_not_contain=raw.get("body_must_not_contain"),
#                 stop_on_fail=bool(raw.get("stop_on_fail", False)),
#                 extract_regex=raw.get("extract_regex"),
#                 store_as=raw.get("store_as"),
#                 require_extracted=bool(raw.get("require_extracted", False)),
#             )
#             steps.append(step)

#         if steps:
#             flows[flow_name] = Flow(
#                 name=flow_name,
#                 description=description,
#                 steps=steps,
#             )

#     return flows

def load_flows(path: Path | None = None) -> Dict[str, Flow]:
    """
    Load all flows from a TOML file, plus any additional TOML files
    in a sibling `flows/` directory.

    Precedence on name collisions:
      - Flows in the main file (flows.toml) are loaded first
      - Flows in flows/*.toml are loaded afterwards and override by name

    Expected shape in each TOML file:

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
    # 1) Resolve the main file
    if path is None:
        path = Path("flows.toml")
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"No flows file found at {path!s}")

    # 2) Load base flows from the main file
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)

    combined_flows: Dict[str, Any] = {}
    base_flows_section = data.get("flows", {})
    if isinstance(base_flows_section, dict):
        combined_flows.update(base_flows_section)

    # 3) Load any additional flows from `flows/*.toml` (sibling directory)
    flows_dir = path.parent / "flows"
    if flows_dir.is_dir():
        for child in sorted(flows_dir.glob("*.toml")):
            child_text = child.read_text(encoding="utf-8")
            child_data = tomllib.loads(child_text)
            child_flows = child_data.get("flows", {})
            if isinstance(child_flows, dict):
                # Later files override earlier by name
                combined_flows.update(child_flows)

    if not isinstance(combined_flows, dict):
        raise ValueError("flows.toml (and any flows/*.toml) must contain a [flows] table.")

    flows: Dict[str, Flow] = {}

    # 4) Build Flow / FlowStep objects from the merged flows dict
    for flow_name, cfg in combined_flows.items():
        if not isinstance(cfg, dict):
            continue

        description = cfg.get("description", "")
        steps_order = cfg.get("steps", [])
        if not isinstance(steps_order, list) or not steps_order:
            # Require explicit step order
            continue

        # Gather step definitions from nested tables
        step_defs: Dict[str, Any] = {}
        for key, value in cfg.items():
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

def _apply_template_functions(template: str, vars: Dict[str, Any]) -> str:
    """
    Expand `{var}` and `{func(var)}` placeholders using a small
    library of template functions: urlencode, upper, lower, strip.
    Unknown functions fall back to the raw value (no transform).
    """

    def _repl(match: re.Match) -> str:
        expr = match.group(1).strip()

        # func(var) form, e.g. urlencode(marker)
        func_name: Optional[str] = None
        var_name = expr

        if "(" in expr and expr.endswith(")"):
            func_name, inner = expr.split("(", 1)
            func_name = func_name.strip()
            var_name = inner[:-1].strip()  # drop closing ")"

        # Lookup variable
        if var_name not in vars:
            # Unknown var → leave placeholder as-is
            return match.group(0)

        value = str(vars[var_name])

        # No function → simple substitution
        if not func_name:
            return value

        # Apply supported functions
        if func_name == "urlencode":
            return quote_plus(value)
        if func_name == "upper":
            return value.upper()
        if func_name == "lower":
            return value.lower()
        if func_name == "strip":
            return value.strip()

        # Unknown function → just use raw value
        return value

    try:
        return _TEMPLATE_RE.sub(_repl, template)
    except Exception:
        # Best effort – on any parsing error, return the original
        return template


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

            # Variable interpolation helper with tiny template functions
            # Supported patterns:
            #   {marker}                  → plain .format(**state["vars"])
            #   {lower(marker)}           → value.lower()
            #   {upper(marker)}           → value.upper()
            #   {strip(marker)}           → value.strip()
            #   {urlencode(marker)}       → urllib.parse.quote_plus(value)
            # Variable interpolation helper with template functions
            def interpolate(template: Optional[str]) -> Optional[str]:
                if template is None:
                    return None
                return _apply_template_functions(template, state["vars"])


                text = template

                # First pass: handle {func(var)} patterns
                def _apply_func(match: re.Match) -> str:
                    func_name, var_name = match.group(1), match.group(2)
                    raw = state["vars"].get(var_name)
                    if raw is None:
                        # If the var doesn't exist yet, leave it untouched
                        return match.group(0)

                    value = str(raw)

                    if func_name == "lower":
                        return value.lower()
                    elif func_name == "upper":
                        return value.upper()
                    elif func_name == "strip":
                        return value.strip()
                    elif func_name == "urlencode":
                        return urllib.parse.quote_plus(value)
                    else:
                        # Unknown function – leave the original text in place
                        return match.group(0)

                # Replace any {func(var)} occurrences
                text = re.sub(r"\{(\w+)\((\w+)\)\}", _apply_func, text)

                # Second pass: normal `{var}` interpolation
                try:
                    return text.format(**state["vars"])
                except KeyError:
                    # Missing vars – just return the partially-processed template
                    return text


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

                # Only capture body for logging when assertions failed
                body_for_log: Optional[str] = None
                if not ok:
                    if body_text is None:
                        try:
                            body_text = resp.text
                        except Exception:
                            body_text = None
                    body_for_log = body_text

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
                        "body": body_for_log,
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
                        "body": None,
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

