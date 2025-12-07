from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

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
