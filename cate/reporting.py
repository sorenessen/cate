# cate/reporting.py
from __future__ import annotations

import json
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
        return f"{x:.3f}".rstrip("0").rstrip(".")
    return str(x)


def _artifact_name(path: Optional[str]) -> str:
    if not path:
        return "—"
    p = Path(path)
    return p.name if p.exists() else "—"


def _best_counts(kind: str, summary: Optional[Dict[str, Any]], signals: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Return the best-available counts block for this run.
    Prefer signals.counts (because signals may exist even when summary.json is disabled).
    Fall back to summary keys.
    """
    counts: Dict[str, Any] = {}

    sig_counts = (signals or {}).get("counts")
    if isinstance(sig_counts, dict):
        counts.update(sig_counts)

    # For flow summaries we also have summary["steps"]/["failures"]
    if kind == "http-flow" and summary:
        if "steps" not in counts and "steps" in summary:
            counts["steps"] = summary.get("steps")
        if "failures" not in counts and "failures" in summary:
            counts["failures"] = summary.get("failures")
        if "failure_rate" not in counts and "steps" in summary and "failures" in summary:
            try:
                s = float(summary.get("steps") or 0)
                f = float(summary.get("failures") or 0)
                counts["failure_rate"] = (f / s) if s > 0 else 0.0
            except Exception:
                pass

    # For fuzz summaries we have different keys
    if kind == "http-fuzz" and summary:
        for k in ("total_payloads", "error_count", "error_rate", "status_counts"):
            if k not in counts and k in summary:
                counts[k] = summary.get(k)

    return counts


def _best_latency(summary: Optional[Dict[str, Any]], signals: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Prefer signals.latency; fall back to summary.avg_latency_ms or summary.latency.
    """
    latency: Dict[str, Any] = {}

    sig_lat = (signals or {}).get("latency")
    if isinstance(sig_lat, dict):
        latency.update(sig_lat)

    if latency:
        return latency

    if isinstance(summary, dict):
        # flow summary.json shape uses avg_latency_ms
        if "avg_latency_ms" in summary:
            latency["avg_ms"] = summary.get("avg_latency_ms")
        # fuzz run summary has "latency" object
        s_lat = summary.get("latency")
        if isinstance(s_lat, dict):
            # map mean_ms -> avg_ms for consistency if present
            if "avg_ms" not in latency and "mean_ms" in s_lat:
                latency["avg_ms"] = s_lat.get("mean_ms")
            for k, v in s_lat.items():
                latency.setdefault(k, v)

    return latency


def _best_ok(kind: str, summary: Optional[Dict[str, Any]], signals: Optional[Dict[str, Any]]) -> Optional[bool]:
    """
    Compute OK using the best available source:
      1) signals.ok if present
      2) summary-derived (failures==0 for flow; error_count==0 and error_rate==0 for fuzz)
      3) None if insufficient
    """
    if isinstance(signals, dict) and "ok" in signals:
        return bool(signals.get("ok"))

    if not isinstance(summary, dict) or not summary:
        return None

    if kind == "http-flow":
        if "failures" in summary:
            try:
                return int(summary.get("failures") or 0) == 0
            except Exception:
                return None

    if kind == "http-fuzz":
        try:
            ec = int(summary.get("error_count") or 0) if "error_count" in summary else None
            er = float(summary.get("error_rate") or 0.0) if "error_rate" in summary else None
            if ec is None and er is None:
                return None
            return (ec or 0) == 0 and (er or 0.0) == 0.0
        except Exception:
            return None

    return None


def _render_exec_summary(
    *,
    kind: str,
    ok: Optional[bool],
    counts: Dict[str, Any],
    latency: Dict[str, Any],
    signals: Optional[Dict[str, Any]],
) -> str:
    """
    Executive summary is designed to be correct even if some artifacts are missing.
    """
    notes = (signals or {}).get("notes") or []
    if not isinstance(notes, list):
        notes = []
    notes_str = ", ".join(sorted(str(n) for n in notes)) if notes else "none"

    lines: list[str] = []
    lines.append("## Executive summary")
    lines.append("")

    # Determine steps/failures where possible
    steps = counts.get("steps")
    failures = counts.get("failures")

    # Confidence heuristic
    has_counts = steps is not None and failures is not None
    has_latency = bool(latency.get("avg_ms") is not None or latency.get("mean_ms") is not None)
    has_top_failure = isinstance((signals or {}).get("top_failure"), dict) and (signals or {}).get("top_failure")
    confidence = "high" if (ok is not None and has_counts and has_latency) else "medium" if (ok is not None and (has_counts or has_latency or has_top_failure)) else "low"

    # Main sentence
    if ok is True:
        if has_counts:
            lines.append(f"No actionable issues detected. Flow completed successfully with {failures} failures across {steps} step(s).")
        else:
            lines.append("No actionable issues detected. Run completed successfully.")
    elif ok is False:
        # Flow failure language (works for fuzz too, but still ok)
        if kind == "http-flow" and has_counts:
            lines.append(f"Immediate attention recommended. Flow failed ({failures}/{steps} steps).")
        else:
            lines.append("Immediate attention recommended. Run indicates failures.")
        tf = (signals or {}).get("top_failure")
        if kind == "http-flow" and isinstance(tf, dict) and tf:
            step = tf.get("step")
            expected = tf.get("expected")
            status = tf.get("status")
            if step is not None and (expected is not None or status is not None):
                lines[-1] += f" Top failure: step {step} expected {expected} got {status}."
    else:
        lines.append("Outcome unknown. Required artifacts were missing, so CATE could not determine pass/fail confidently.")

    lines.append("")
    # Key findings
    kf: list[str] = []

    if kind == "http-flow":
        tf = (signals or {}).get("top_failure")
        if isinstance(tf, dict) and tf:
            kf.append(f"- Top failure at step `{tf.get('step')}`: expected `{tf.get('expected')}`, got `{tf.get('status')}`.")
            if tf.get("error"):
                kf.append(f"- Failure message: `{tf.get('error')}`.")

    if kind == "http-fuzz":
        tt = (signals or {}).get("top_trigger")
        if isinstance(tt, str) and tt:
            kf.append(f"- Top trigger payload: `{tt}`.")

    avg_ms = latency.get("avg_ms")
    if avg_ms is None and "mean_ms" in latency:
        avg_ms = latency.get("mean_ms")
    if avg_ms is not None:
        kf.append(f"- Average step latency: `{_fmt_num(avg_ms)} ms`.")
    if notes_str != "none":
        kf.append(f"- Signals notes: `{notes_str}`.")

    if kf:
        lines.append("### Key findings")
        lines.append("")
        lines.extend(kf)
        lines.append("")

    # Recommended actions only when failing
    if ok is False and kind == "http-flow":
        ra: list[str] = []
        tf = (signals or {}).get("top_failure")
        if isinstance(tf, dict) and tf:
            step = tf.get("step")
            exp = tf.get("expected")
            got = tf.get("status")
            ra.append(f"- Confirm step `{step}` expectation is correct (expected `{exp}`); update flow contract if intentional.")
            if str(got) == "302":
                ra.append("- If redirects are expected, assert redirect status and validate the `Location` header target.")
                ra.append("- If redirects are not expected, review gateway/proxy rules and authentication entrypoints for unintended redirects.")
        if ra:
            lines.append("### Recommended actions")
            lines.append("")
            lines.extend(ra)
            lines.append("")

    lines.append(f"**Confidence:** `{confidence}`")
    return "\n".join(lines).rstrip()


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
    notes = (signals or {}).get("notes") or []
    if not isinstance(notes, list):
        notes = []
    notes = sorted(str(n) for n in notes)

    counts = _best_counts(kind, summary, signals)
    latency = _best_latency(summary, signals)
    ok = _best_ok(kind, summary, signals)

    lines: list[str] = []
    lines.append(f"# CATE Report — {severity}")
    lines.append("")
    lines.append(_render_exec_summary(kind=kind, ok=ok, counts=counts, latency=latency, signals=signals))
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|------|-------|")
    lines.append(f"| Kind | `{kind}` |")
    lines.append(f"| OK | `{_fmt_bool(ok) if ok is not None else '—'}` |")
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

    # Counts
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

    # Latency
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

    # Artifacts table
    lines.append("## Artifacts")
    lines.append("")
    lines.append("| Artifact | File |")
    lines.append("|---|---|")
    lines.append(f"| Summary (json) | `{_artifact_name(summary_json_path)}` |")
    lines.append(f"| Summary (md) | `{_artifact_name(summary_md_path)}` |")
    lines.append(f"| Signals (json) | `{_artifact_name(signals_json_path)}` |")
    lines.append(f"| Signals (md) | `{_artifact_name(signals_md_path)}` |")
    lines.append("")

    # Optional summary snapshot
    if isinstance(summary, dict) and summary:
        lines.append("## Summary snapshot")
        lines.append("")
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
