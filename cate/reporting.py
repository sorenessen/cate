# cate/reporting.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def _normalize_output_prefix(output_prefix: str) -> Path:
    out = Path(output_prefix)
    if out.suffix.lower() == ".jsonl":
        out = out.with_suffix("")
    return out


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fmt_bool(b: Any) -> str:
    return "True" if bool(b) else "False"


def _fmt_num(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        # keep diffs stable but still readable
        return f"{x:.3f}".rstrip("0").rstrip(".")
    return str(x)


def _artifact_name(path: Optional[str]) -> str:
    if not path:
        return "—"
    return Path(path).name


def render_report_md(
    *,
    kind: str,
    env: Optional[str],
    summary: Optional[Dict[str, Any]],
    signals: Optional[Dict[str, Any]],
    summary_json_path: Optional[str],
    summary_md_path: Optional[str],
    signals_json_path: Optional[str],
    signals_md_path: Optional[str],
) -> str:
    kind = str(kind or (signals or {}).get("kind") or (summary or {}).get("kind") or "run")
    severity = str((signals or {}).get("severity", "none")).upper()
    ok = bool((signals or {}).get("ok", False))
    notes = (signals or {}).get("notes") or []
    if not isinstance(notes, list):
        notes = []
    notes = sorted(str(n) for n in notes)

    lines: list[str] = []
    lines.append(f"# CATE Report — {severity}")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|------|-------|")
    lines.append(f"| Kind | `{kind}` |")
    lines.append(f"| OK | `{_fmt_bool(ok)}` |")
    if env:
        lines.append(f"| Env | `{env}` |")
    lines.append(f"| Notes | `{', '.join(notes) if notes else 'none'}` |")
    lines.append("")

    # Primary finding block
    tf = (signals or {}).get("top_failure")
    tt = (signals or {}).get("top_trigger")

    if kind == "http-flow" and isinstance(tf, dict) and tf:
        lines.append("## Primary finding")
        lines.append("")
        lines.append("### Top failure")
        step = tf.get("step")
        expected = tf.get("expected")
        status = tf.get("status")
        error = tf.get("error")
        if step is not None:
            lines.append(f"- Step: **{step}**")
        if expected is not None:
            lines.append(f"- Expected: **{expected}**")
        if status is not None:
            lines.append(f"- Got: **{status}**")
        if error:
            lines.append(f"- Error: `{error}`")
        lines.append("")

    if kind == "http-fuzz" and isinstance(tt, str) and tt:
        lines.append("## Primary finding")
        lines.append("")
        lines.append("### Top trigger")
        lines.append(f"- Payload: `{tt}`")
        lines.append("")

    # Counts + latency (reuse signals structure)
    counts = (signals or {}).get("counts") or {}
    latency = (signals or {}).get("latency") or {}

    if isinstance(counts, dict) and counts:
        lines.append("## Counts")
        lines.append("")
        if kind == "http-flow":
            for k, label in (("steps", "Steps"), ("failures", "Failures"), ("failure_rate", "Failure rate")):
                if k in counts:
                    lines.append(f"- {label}: **{_fmt_num(counts.get(k))}**")
        elif kind == "http-fuzz":
            for k, label in (("total_payloads", "Total payloads"), ("error_count", "Error count"), ("error_rate", "Error rate")):
                if k in counts:
                    lines.append(f"- {label}: **{_fmt_num(counts.get(k))}**")

            status_counts = counts.get("status_counts")
            if isinstance(status_counts, dict) and status_counts:
                lines.append("")
                lines.append("### Status codes")
                lines.append("")
                def _sk(x: Any):
                    try:
                        return (0, int(x))
                    except Exception:
                        return (1, str(x))
                for code in sorted(status_counts.keys(), key=_sk):
                    lines.append(f"- `{code}`: **{status_counts[code]}**")
        else:
            for k in sorted(counts.keys()):
                lines.append(f"- {k}: **{_fmt_num(counts.get(k))}**")
        lines.append("")

    if isinstance(latency, dict) and latency:
        lines.append("## Latency (ms)")
        lines.append("")
        preferred = ["avg_ms", "p50_ms", "p90_ms", "p99_ms"]
        seen = set()
        for k in preferred:
            if k in latency:
                lines.append(f"- `{k}`: **{_fmt_num(latency.get(k))}**")
                seen.add(k)
        for k in sorted(latency.keys()):
            if k not in seen:
                lines.append(f"- `{k}`: **{_fmt_num(latency.get(k))}**")
        lines.append("")

    # Artifacts table (this is the “shareable” part)
    lines.append("## Artifacts")
    lines.append("")
    lines.append("| Artifact | File |")
    lines.append("|---|---|")
    lines.append(f"| Summary (json) | `{_artifact_name(summary_json_path)}` |")
    lines.append(f"| Summary (md) | `{_artifact_name(summary_md_path)}` |")
    lines.append(f"| Signals (json) | `{_artifact_name(signals_json_path)}` |")
    lines.append(f"| Signals (md) | `{_artifact_name(signals_md_path)}` |")
    lines.append("")

    # Optional: echo a tiny excerpt of summary shape (helps debugging)
    if isinstance(summary, dict) and summary:
        lines.append("## Summary snapshot")
        lines.append("")
        # stable key subset
        snap_keys = ["steps", "failures", "avg_latency_ms", "total_payloads", "error_count", "error_rate"]
        for k in snap_keys:
            if k in summary:
                lines.append(f"- `{k}`: **{_fmt_num(summary.get(k))}**")
        lines.append("")

    lines.append("---")
    lines.append("_Generated by CATE_")
    return "\n".join(lines).rstrip() + "\n"


def write_report_md(
    *,
    output_prefix: str,
    env: Optional[str] = None,
    kind: Optional[str] = None,
    summary_json_path: Optional[str] = None,
    summary_md_path: Optional[str] = None,
    signals_json_path: Optional[str] = None,
    signals_md_path: Optional[str] = None,
) -> str:
    """
    Writes <prefix>.report.md next to your other artifacts and returns the path.
    Best-effort: if it can't read json artifacts, it still writes a report shell.
    """
    out = _normalize_output_prefix(output_prefix)
    report_path = out.with_suffix(".report.md")

    summary = _safe_read_json(Path(summary_json_path)) if summary_json_path else None
    signals = _safe_read_json(Path(signals_json_path)) if signals_json_path else None

    effective_kind = str(kind or (signals or {}).get("kind") or (summary or {}).get("kind") or "run")

    md = render_report_md(
        kind=effective_kind,
        env=env,
        summary=summary,
        signals=signals,
        summary_json_path=summary_json_path,
        summary_md_path=summary_md_path,
        signals_json_path=signals_json_path,
        signals_md_path=signals_md_path,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")
    return str(report_path)
