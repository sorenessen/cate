from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import asyncio
import re
import time
import uuid
import secrets
import string
from datetime import datetime, timezone

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

    # Variables / extractors (v0.3.1 / v0.3.3)
    extract_regex: Optional[str] = None     # regex-based extractor (body)
    extract_json: Optional[str] = None      # JSON path extractor, e.g. "data.items[0].token"
    extract_header: Optional[str] = None    # header extractor, e.g. "Set-Cookie"
    store_as: Optional[str] = None
    require_extracted: bool = False

    # Header assertions (v0.3.4)
    header_must_exist: Optional[List[str]] = None        # e.g. ["Content-Type", "ETag"]
    header_must_contain: Optional[Dict[str, str]] = None # e.g. { "Content-Type" = "application/json" }
    header_must_equal: Optional[Dict[str, str]] = None   # e.g. { "X-Env" = "dev" }

    # JSON assertions (v0.3.4)
    json_must_exist: Optional[List[str]] = None          # e.g. ["data.id", "data.user.email"]
    json_must_equal: Optional[Dict[str, str]] = None     # e.g. { "completed" = "false" }
    json_must_contain: Optional[Dict[str, str]] = None   # e.g. { "title" = "delectus" }

    # Cookie extractors / assertions (v0.3.4)
    extract_cookie: Optional[str] = None          # cookie name, e.g. "sessionid"
    cookie_strategy: Optional[str] = None         # "first" | "last" | "all" (default "last")

    cookie_must_exist: Optional[List[str]] = None
    cookie_must_equal: Optional[Dict[str, str]] = None
    cookie_must_contain: Optional[Dict[str, str]] = None

    # Redirect + retries (v0.3.5)
    follow_redirects: Optional[bool] = None  # None = default (True)

    retry_count: int = 0
    retry_backoff_ms: int = 250
    retry_on_status: Optional[List[int]] = None  # e.g. [429, 500, 502, 503, 504]
    retry_on_timeout: bool = True

@dataclass
class Flow:
    name: str
    description: str
    steps: List[FlowStep]

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

            raw_header_must_exist = raw.get("header_must_exist")
            header_must_exist: Optional[List[str]] = None
            if isinstance(raw_header_must_exist, str):
                header_must_exist = [raw_header_must_exist]
            elif isinstance(raw_header_must_exist, list):
                header_must_exist = [str(h) for h in raw_header_must_exist]

            raw_header_must_contain = raw.get("header_must_contain")
            header_must_contain: Optional[Dict[str, str]] = None
            if isinstance(raw_header_must_contain, dict):
                header_must_contain = {
                    str(k): str(v) for k, v in raw_header_must_contain.items()
                }

            raw_header_must_equal = raw.get("header_must_equal")
            header_must_equal: Optional[Dict[str, str]] = None
            if isinstance(raw_header_must_equal, dict):
                header_must_equal = {
                    str(k): str(v) for k, v in raw_header_must_equal.items()
                }


            raw_json_must_exist = raw.get("json_must_exist")
            json_must_exist: Optional[List[str]] = None
            if isinstance(raw_json_must_exist, str):
                json_must_exist = [raw_json_must_exist]
            elif isinstance(raw_json_must_exist, list):
                json_must_exist = [str(p) for p in raw_json_must_exist]

            raw_json_must_equal = raw.get("json_must_equal")
            json_must_equal: Optional[Dict[str, str]] = None
            if isinstance(raw_json_must_equal, dict):
                json_must_equal = {
                    str(k): str(v) for k, v in raw_json_must_equal.items()
                }

            raw_json_must_contain = raw.get("json_must_contain")
            json_must_contain: Optional[Dict[str, str]] = None
            if isinstance(raw_json_must_contain, dict):
                json_must_contain = {
                    str(k): str(v) for k, v in raw_json_must_contain.items()
                }

            follow_redirects = raw.get("follow_redirects")

            retry_count = raw.get("retry_count", 0)
            retry_backoff_ms = raw.get("retry_backoff_ms", 250)

            raw_retry_on_status = raw.get("retry_on_status")
            retry_on_status: Optional[List[int]] = None
            if isinstance(raw_retry_on_status, int):
                retry_on_status = [int(raw_retry_on_status)]
            elif isinstance(raw_retry_on_status, list):
                out: List[int] = []
                for x in raw_retry_on_status:
                    try:
                        out.append(int(x))
                    except Exception:
                        pass
                retry_on_status = out or None



            retry_on_timeout = bool(raw.get("retry_on_timeout", True))


            extract_cookie = raw.get("extract_cookie")
            cookie_strategy = raw.get("cookie_strategy")

            # cookie_must_exist can be string or list
            raw_cookie_must_exist = raw.get("cookie_must_exist")
            cookie_must_exist: Optional[List[str]] = None
            if isinstance(raw_cookie_must_exist, str):
                cookie_must_exist = [raw_cookie_must_exist]
            elif isinstance(raw_cookie_must_exist, list):
                cookie_must_exist = [str(x) for x in raw_cookie_must_exist]

            raw_cookie_must_equal = raw.get("cookie_must_equal")
            cookie_must_equal: Optional[Dict[str, str]] = None
            if isinstance(raw_cookie_must_equal, dict):
                cookie_must_equal = {str(k): str(v) for k, v in raw_cookie_must_equal.items()}

            raw_cookie_must_contain = raw.get("cookie_must_contain")
            cookie_must_contain: Optional[Dict[str, str]] = None
            if isinstance(raw_cookie_must_contain, dict):
                cookie_must_contain = {str(k): str(v) for k, v in raw_cookie_must_contain.items()}


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
                extract_json=raw.get("extract_json"),
                extract_header=raw.get("extract_header"),
                store_as=raw.get("store_as"),
                require_extracted=bool(raw.get("require_extracted", False)),
                header_must_exist=header_must_exist,
                header_must_contain=header_must_contain,
                header_must_equal=header_must_equal,
                json_must_exist=json_must_exist,
                json_must_equal=json_must_equal,
                json_must_contain=json_must_contain,
                extract_cookie=extract_cookie,
                cookie_strategy=cookie_strategy,
                cookie_must_exist=cookie_must_exist,
                cookie_must_equal=cookie_must_equal,
                cookie_must_contain=cookie_must_contain,
                follow_redirects=follow_redirects,
                retry_count=int(retry_count or 0),
                retry_backoff_ms=int(retry_backoff_ms or 250),
                retry_on_status=retry_on_status,
                retry_on_timeout=retry_on_timeout,


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
    Expand placeholders like:

      {var}
      {upper(var)}
      {lower(var)}
      {strip(var)}
      {urlencode(var)}

    and also function-only helpers that do *not* depend on vars:

      {uuid()}           -> random UUID4 string
      {timestamp()}      -> current UTC ISO8601 timestamp
      {random(8)}        -> random 8-char alphanumeric string
    """

    def _repl(match: re.Match) -> str:
        expr = match.group(1).strip()

        # --- 0.3.2: function-only helpers (no vars) -------------------------
        # {uuid()}
        if expr == "uuid()":
            return str(uuid.uuid4())

        # {timestamp()}
        if expr == "timestamp()":
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # {random(8)} or {random()} (default length 8)
        m_random = re.fullmatch(r"random\(\s*(\d*)\s*\)", expr)
        if m_random:
            length_str = m_random.group(1)
            try:
                length = int(length_str) if length_str else 8
            except ValueError:
                length = 8

            # sanity clamp
            length = max(1, min(length, 256))
            alphabet = string.ascii_letters + string.digits
            return "".join(secrets.choice(alphabet) for _ in range(length))

        # --- Existing {var} / {func(var)} behavior --------------------------
        func_name: Optional[str] = None
        var_name = expr

        # func(var) form, e.g. urlencode(marker)
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
    initial_vars: Optional[Dist[str, Any]] = None,
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
    state: Dict[str, Any] = {
        "cookies": httpx.Cookies(),
        # Seed with any CLI / external vars; copy to avoid mutating caller dict
        "vars": dict(initial_vars or {}),
    }

    results: List[Dict[str, Any]] = []

    async with httpx.AsyncClient(
        timeout=timeout,
        cookies=state["cookies"],
        follow_redirects=True,
    ) as client:

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

            # Variable interpolation helper with template functions
            # Supported patterns:
            #   {marker}
            #   {lower(marker)}
            #   {upper(marker)}
            #   {strip(marker)}
            #   {urlencode(marker)}
            # and function-only helpers:
            #   {uuid()}
            #   {timestamp()}
            #   {random(8)}
            def interpolate(template: Optional[str]) -> Optional[str]:
                if template is None:
                    return None
                return _apply_template_functions(template, state["vars"])


            url = interpolate(url_template) or url_template
            data = interpolate(body_template)

            # per-step headers with interpolation
            headers: Optional[Dict[str, str]] = None
            if step.headers:
                headers = {
                    key: interpolate(value) or value
                    for key, value in step.headers.items()
                }

            headers_dict: Dict[str, str] = {}    

            started = time.perf_counter()
            try:
                # Per-step override: default True (browser-like)
                step_follow = True if step.follow_redirects is None else bool(step.follow_redirects)

                # Retry settings
                retry_count = int(getattr(step, "retry_count", 0) or 0)
                backoff_ms = int(getattr(step, "retry_backoff_ms", 250) or 250)
                retry_on_status = getattr(step, "retry_on_status", None) or [429, 500, 502, 503, 504]
                retry_on_timeout = bool(getattr(step, "retry_on_timeout", True))

                resp = None
                last_exc: Optional[Exception] = None
                attempts = 0

                for attempt in range(retry_count + 1):
                    attempts = attempt + 1
                    try:
                        resp = await client.request(
                            method,
                            url,
                            data=data,
                            headers=headers,
                            follow_redirects=step_follow,
                        )

                        # Retry on selected status codes (transient)
                        if resp.status_code in retry_on_status and attempt < retry_count:
                            sleep_s = (backoff_ms * (2 ** attempt)) / 1000.0
                            await asyncio.sleep(sleep_s)
                            continue

                        # Otherwise accept this response
                        break

                    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.TimeoutException) as exc:
                        last_exc = exc
                        if retry_on_timeout and attempt < retry_count:
                            sleep_s = (backoff_ms * (2 ** attempt)) / 1000.0
                            await asyncio.sleep(sleep_s)
                            continue
                        raise

                if resp is None:
                    # Should be unreachable, but keep it safe
                    if last_exc:
                        raise last_exc
                    raise RuntimeError("request failed without response")


                elapsed_ms = (time.perf_counter() - started) * 1000.0
                status = resp.status_code
                size = len(resp.content)
                body_text: Optional[str] = None  # lazy
                headers_dict: Dict[str, str] = dict(resp.headers)
                
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

                # Header assertions
                if step.header_must_exist:
                    missing: List[str] = []
                    for name in step.header_must_exist:
                        target = str(name).lower()
                        found = any(h.lower() == target for h in headers_dict.keys())
                        if not found:
                            missing.append(str(name))

                    if missing:
                        assertions["headers_exist_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            f"missing required header(s): {', '.join(missing)}"
                        )
                    else:
                        assertions["headers_exist_ok"] = True

                if step.header_must_contain:
                    bad_contains: List[str] = []
                    for name, expected in step.header_must_contain.items():
                        target = name.lower()
                        value = None
                        for hk, hv in headers_dict.items():
                            if hk.lower() == target:
                                value = hv
                                break

                        if value is None or str(expected) not in str(value):
                            bad_contains.append(f"{name!r} !~ {expected!r}")

                    if bad_contains:
                        assertions["headers_contain_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            "header content mismatch: " + ", ".join(bad_contains)
                        )
                    else:
                        assertions["headers_contain_ok"] = True

                if step.header_must_equal:
                    bad_equal: List[str] = []
                    for name, expected in step.header_must_equal.items():
                        target = name.lower()
                        value = None
                        for hk, hv in headers_dict.items():
                            if hk.lower() == target:
                                value = hv
                                break

                        if value is None:
                            bad_equal.append(f"{name!r} missing for equality check")
                        elif str(value) != str(expected):
                            bad_equal.append(
                                f"{name!r} = {value!r} != expected {expected!r}"
                            )

                    if bad_equal:
                        assertions["headers_equal_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            "header equality mismatch: " + ", ".join(bad_equal)
                        )
                    else:
                        assertions["headers_equal_ok"] = True

                # Cookie assertions (checks current cookie jar state)
                if step.cookie_must_exist:
                    missing: List[str] = []
                    for name in step.cookie_must_exist:
                        if client.cookies.get(str(name)) is None:
                            missing.append(str(name))
                    if missing:
                        assertions["cookies_exist_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            f"missing required cookie(s): {', '.join(missing)}"
                        )
                    else:
                        assertions["cookies_exist_ok"] = True

                if step.cookie_must_contain:
                    bad: List[str] = []
                    for name, expected in step.cookie_must_contain.items():
                        val = client.cookies.get(str(name))
                        if val is None or str(expected) not in str(val):
                            bad.append(f"{name!r} !~ {expected!r}")
                    if bad:
                        assertions["cookies_contain_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            "cookie content mismatch: " + ", ".join(bad)
                        )
                    else:
                        assertions["cookies_contain_ok"] = True

                if step.cookie_must_equal:
                    bad: List[str] = []
                    for name, expected in step.cookie_must_equal.items():
                        val = client.cookies.get(str(name))
                        if val is None:
                            bad.append(f"{name!r} missing for equality check")
                        elif str(val) != str(expected):
                            bad.append(f"{name!r} = {val!r} != expected {expected!r}")
                    if bad:
                        assertions["cookies_equal_ok"] = False
                        ok = False
                        error_msg_parts.append(
                            "cookie equality mismatch: " + ", ".join(bad)
                        )
                    else:
                        assertions["cookies_equal_ok"] = True



                # Extractor / variable assertion
                extracted_var = None
                extracted_value = None

                # --- JSON helpers (used by JSON assertions and extract_json) ---
                json_obj: Any = None
                json_parsed: bool = False
                json_parse_error: Optional[str] = None

                def _ensure_json_loaded() -> Optional[Any]:
                    nonlocal json_obj, json_parsed, json_parse_error, body_text
                    if json_parsed:
                        return json_obj
                    if body_text is None:
                        body_text = resp.text
                    try:
                        json_obj = resp.json()
                        json_parse_error = None
                    except Exception as exc:
                        json_obj = None
                        json_parse_error = str(exc)
                    json_parsed = True
                    return json_obj

                def _extract_json_path(obj: Any, path: str) -> Any:
                    """
                    Simple dotted path with optional [index], e.g.:
                      "id"
                      "data.token"
                      "items[0].id"
                      "outer.inner[2].value"
                    """
                    current = obj
                    for segment in path.split("."):
                        if not isinstance(current, (dict, list)):
                            return None

                        # Match "key" or "key[0]"
                        m_seg = re.match(r"^([^\[\]]+)(\[(\d+)\])?$", segment)
                        if not m_seg:
                            return None

                        key = m_seg.group(1)
                        idx_str = m_seg.group(3)

                        if isinstance(current, dict):
                            if key not in current:
                                return None
                            current = current[key]
                        else:
                            # trying to use a dict-style key on a list
                            return None

                        if idx_str is not None:
                            if not isinstance(current, list):
                                return None
                            idx = int(idx_str)
                            if idx < 0 or idx >= len(current):
                                return None
                            current = current[idx]

                    return current

                # --- JSON assertions (optional) ---
                needs_json_assertions = (
                    (step.json_must_exist and len(step.json_must_exist) > 0)
                    or (step.json_must_equal and len(step.json_must_equal) > 0)
                    or (step.json_must_contain and len(step.json_must_contain) > 0)
                )

                if needs_json_assertions:
                    obj = _ensure_json_loaded()
                    if obj is None:
                        assertions["json_ok"] = False
                        ok = False
                        msg = "failed to parse JSON body for JSON assertions"
                        if json_parse_error:
                            msg += f" ({json_parse_error})"
                        error_msg_parts.append(msg)
                    else:
                        json_errors: List[str] = []

                        # Existence checks
                        if step.json_must_exist:
                            for path in step.json_must_exist:
                                val = _extract_json_path(obj, path)
                                if val is None:
                                    # add top-level key hint if we can
                                    extra = ""
                                    if isinstance(obj, dict):
                                        keys = list(obj.keys())
                                        if keys:
                                            top_keys = ", ".join(repr(k) for k in keys[:8])
                                            extra = f" (top-level keys: {top_keys})"
                                    json_errors.append(
                                        f"missing JSON path {path!r}{extra}"
                                    )
                        
                        # Equality checks
                        if step.json_must_equal:
                            for path, expected in step.json_must_equal.items():
                                val = _extract_json_path(obj, path)
                                if val is None:
                                    json_errors.append(
                                        f"JSON path {path!r} not found for equality check"
                                    )
                                else:
                                    if str(val) != str(expected):
                                        json_errors.append(
                                            f"JSON path {path!r} = {val!r} != expected {expected!r}"
                                        )

                        # Contains checks (substring)
                        if step.json_must_contain:
                            for path, expected in step.json_must_contain.items():
                                val = _extract_json_path(obj, path)
                                if val is None:
                                    json_errors.append(
                                        f"JSON path {path!r} not found for contain check"
                                    )
                                else:
                                    if str(expected) not in str(val):
                                        json_errors.append(
                                            f"JSON path {path!r} value {val!r} does not contain {expected!r}"
                                        )

                        if json_errors:
                            assertions["json_ok"] = False
                            ok = False
                            error_msg_parts.append(
                                "JSON assertion failures: " + "; ".join(json_errors)
                            )
                        else:
                            assertions["json_ok"] = True

                # --- Extractors (JSON > regex > header) ---

                # Prefer JSON extractor if configured
                if step.extract_json and step.store_as:
                    obj = _ensure_json_loaded()
                    if obj is None:
                        assertions["extracted_ok"] = False
                        if step.require_extracted:
                            ok = False
                            msg = (
                                f"failed to parse JSON body for extract_json path "
                                f"{step.extract_json!r}"
                            )
                            if json_parse_error:
                                msg += f" ({json_parse_error})"
                            error_msg_parts.append(msg)
                    else:
                        extracted = _extract_json_path(obj, step.extract_json)
                        if extracted is not None:
                            extracted_value = extracted
                            extracted_var = step.store_as
                            state["vars"][step.store_as] = extracted_value
                            assertions["extracted_ok"] = True
                        else:
                            assertions["extracted_ok"] = False
                            if step.require_extracted:
                                ok = False
                                extra = ""
                                if isinstance(obj, dict):
                                    keys = list(obj.keys())
                                    if keys:
                                        top_keys = ", ".join(repr(k) for k in keys[:8])
                                        extra = f" (top-level keys: {top_keys})"
                                error_msg_parts.append(
                                    f"failed to extract '{step.store_as}' from JSON path "
                                    f"{step.extract_json!r}{extra}"
                                )

                # Fallback: regex extractor (only if no extract_json)
                elif step.extract_regex and step.store_as:
                    if body_text is None:
                        body_text = resp.text
                    m = re.search(step.extract_regex, body_text, flags=re.DOTALL)
                    if m:
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

                # Fallback: header extractor (only if no JSON/regex extractor)
                elif step.extract_header and step.store_as:
                    target_name = step.extract_header.lower()
                    header_value = None
                    for hk, hv in headers_dict.items():
                        if hk.lower() == target_name:
                            header_value = hv
                            break

                    if header_value is not None:
                        extracted_value = header_value
                        extracted_var = step.store_as
                        state["vars"][step.store_as] = extracted_value
                        assertions["extracted_ok"] = True
                    else:
                        assertions["extracted_ok"] = False
                        if step.require_extracted:
                            ok = False
                            error_msg_parts.append(
                                f"failed to extract header '{step.extract_header}' into '{step.store_as}'"
                            )

                # Fallback: cookie extractor (only if no JSON/regex/header extractor)
                elif step.extract_cookie and step.store_as:
                    cookie_name = str(step.extract_cookie)
                    strategy = (step.cookie_strategy or "last").strip().lower()

                    # Pull matching Set-Cookie headers
                    values: List[str] = []
                    try:
                        set_cookie_headers = resp.headers.get_list("set-cookie")
                    except Exception:
                        set_cookie_headers = []

                    for raw_sc in set_cookie_headers:
                        # raw_sc looks like: "sessionid=abc123; Path=/; HttpOnly"
                        prefix = cookie_name + "="
                        if raw_sc.startswith(prefix):
                            value_part = raw_sc[len(prefix):]
                            value = value_part.split(";", 1)[0]
                            values.append(value)

                    selected: Any = None
                    if values:
                        if strategy == "first":
                            selected = values[0]
                        elif strategy == "all":
                            selected = values
                        else:
                            # default "last"
                            selected = values[-1]

                    if selected is not None:
                        extracted_value = selected
                        extracted_var = step.store_as
                        state["vars"][step.store_as] = extracted_value
                        assertions["extracted_ok"] = True
                    else:
                        assertions["extracted_ok"] = False
                        if step.require_extracted:
                            ok = False
                            error_msg_parts.append(
                                f"failed to extract cookie {cookie_name!r} (strategy={strategy!r}) into '{step.store_as}'"
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
                        "response_headers": headers_dict,
                        "attempts": attempts,
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
                        "error": f"{type(exc).__name__}: {exc}" if str(exc) else f"{type(exc).__name__}",
                        "assertions": {},
                        "extracted_var": None,
                        "extracted_value": None,
                        "headers": headers or {},
                        "body": None,
                        "response_headers": headers_dict,
                        "attempts": attempts or 1,

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
    initial_vars: Optional[Dict[str, Any]] = None,
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
            initial_vars=initial_vars,
        )
    )
