# cate/reporting.py
from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def _safe_read_jsonl(path: Path, limit: int = 2000) -> Optional[List[Dict[str, Any]]]:
    """
    Best-effort JSONL reader. Caps at `limit` rows to avoid giant HTML files by default.
    """
    try:
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except Exception:
                    continue
        return rows
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
    return Path(path).name


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return []


def _get_kind(kind: Optional[str], summary: Optional[Dict[str, Any]], signals: Optional[Dict[str, Any]]) -> str:
    return str(kind or (signals or {}).get("kind") or (summary or {}).get("kind") or "run")


def _get_ok(kind: str, summary: Optional[Dict[str, Any]], signals: Optional[Dict[str, Any]]) -> bool:
    if isinstance(signals, dict) and "ok" in signals:
        return bool(signals.get("ok"))
    # fall back to summary if signals missing
    if isinstance(summary, dict):
        if kind == "http-flow":
            failures = int(summary.get("failures", 0) or 0)
            return failures == 0
        if kind == "http-fuzz":
            error_count = int(summary.get("error_count", 0) or 0)
            return error_count == 0
    return False


def _get_severity(summary: Optional[Dict[str, Any]], signals: Optional[Dict[str, Any]]) -> str:
    sev = str((signals or {}).get("severity", "none")).lower()
    return sev.upper()


def _escape(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _first_nonempty(*vals: Any) -> Optional[Any]:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _build_executive_summary(
    *,
    kind: str,
    env: Optional[str],
    summary: Optional[Dict[str, Any]],
    signals: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Produces:
      - headline (1–2 sentences)
      - key_findings (bullets)
      - recommended_actions (bullets, optional)
      - confidence: high|medium|low
    """
    ok = _get_ok(kind, summary, signals)
    notes = _as_list((signals or {}).get("notes"))
    notes_s = ", ".join(sorted(str(n) for n in notes)) if notes else "none"

    key_findings: List[str] = []
    rec_actions: List[str] = []

    latency_avg = None
    if isinstance(signals, dict):
        latency = signals.get("latency") or {}
        if isinstance(latency, dict) and "avg_ms" in latency:
            latency_avg = latency.get("avg_ms")

    # flow specifics
    tf = (signals or {}).get("top_failure") if isinstance(signals, dict) else None
    if kind == "http-flow":
        steps = None
        failures = None
        if isinstance(signals, dict):
            counts = signals.get("counts") or {}
            if isinstance(counts, dict):
                steps = counts.get("steps")
                failures = counts.get("failures")
        if steps is None and isinstance(summary, dict):
            steps = summary.get("steps")
        if failures is None and isinstance(summary, dict):
            failures = summary.get("failures")

        steps_i = int(steps or 0)
        failures_i = int(failures or 0)

        if ok:
            headline = (
                f"No actionable issues detected. Flow completed successfully with {failures_i} "
                f"failures across {steps_i} step(s)."
            )
            confidence = "high"
        else:
            # default headline
            headline = f"Immediate attention recommended. Flow failed ({failures_i}/{steps_i} steps)."
            confidence = "medium"

            if isinstance(tf, dict) and tf:
                step = tf.get("step")
                exp = tf.get("expected")
                got = tf.get("status")
                headline = (
                    f"Immediate attention recommended. Flow failed ({failures_i}/{steps_i} steps). "
                    f"Top failure: step {step} expected {exp} got {got}."
                )

                err = tf.get("error")
                if step is not None:
                    key_findings.append(f"Top failure at step `{step}`: expected `{exp}`, got `{got}`.")
                if err:
                    key_findings.append(f"Failure message: `{err}`.")
                if got is not None and str(got) == "302":
                    key_findings.append(
                        "Unexpected redirect status observed, which may indicate an auth gateway or routing behavior mismatch."
                    )

                # recommended actions
                if exp is not None:
                    rec_actions.append(f"Confirm step `{step}` expectation is correct (expected {exp}); update flow contract if intentional.")
                rec_actions.append("If redirects are expected, assert redirect status and validate the `Location` header target.")
                rec_actions.append("If redirects are not expected, review gateway/proxy rules and authentication entrypoints for unintended redirects.")

            # notes as context
            if notes and notes_s != "none":
                key_findings.append(f"Signals notes: `{notes_s}`.")

        if latency_avg is not None:
            key_findings.append(f"Average step latency: `{_fmt_num(latency_avg)} ms`.")

        if not key_findings and latency_avg is not None:
            key_findings.append(f"Average step latency: `{_fmt_num(latency_avg)} ms`.")

        return {
            "headline": headline,
            "key_findings": key_findings,
            "recommended_actions": rec_actions,
            "confidence": confidence,
        }

    # fuzz specifics
    if kind == "http-fuzz":
        total = None
        errors = None
        if isinstance(signals, dict):
            counts = signals.get("counts") or {}
            if isinstance(counts, dict):
                total = counts.get("total_payloads")
                errors = counts.get("error_count")
        if total is None and isinstance(summary, dict):
            total = summary.get("total_payloads")
        if errors is None and isinstance(summary, dict):
            errors = summary.get("error_count")

        total_i = int(total or 0)
        errors_i = int(errors or 0)

        if ok:
            headline = f"No actionable issues detected. Fuzz run completed successfully with {errors_i} errors across {total_i} payload(s)."
            confidence = "high"
        else:
            headline = f"Attention recommended. Fuzz run recorded {errors_i} error(s) across {total_i} payload(s)."
            confidence = "medium"

        if latency_avg is not None:
            key_findings.append(f"Average request latency: `{_fmt_num(latency_avg)} ms`.")
        if notes and notes_s != "none":
            key_findings.append(f"Signals notes: `{notes_s}`.")

        return {
            "headline": headline,
            "key_findings": key_findings,
            "recommended_actions": rec_actions,
            "confidence": confidence,
        }

    # default
    headline = "Report generated."
    confidence = "medium"
    if latency_avg is not None:
        key_findings.append(f"Average latency: `{_fmt_num(latency_avg)} ms`.")
    return {
        "headline": headline,
        "key_findings": key_findings,
        "recommended_actions": rec_actions,
        "confidence": confidence,
    }


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
    kind = _get_kind(kind, summary, signals)
    severity = _get_severity(summary, signals)
    ok = _get_ok(kind, summary, signals)
    notes = _as_list((signals or {}).get("notes")) if isinstance(signals, dict) else []
    notes = sorted(str(n) for n in notes)

    exec_sum = _build_executive_summary(kind=kind, env=env, summary=summary, signals=signals)

    lines: list[str] = []
    lines.append(f"# CATE Report — {severity}")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append(exec_sum["headline"])
    lines.append("")
    if exec_sum.get("key_findings"):
        lines.append("### Key findings")
        lines.append("")
        for x in exec_sum["key_findings"]:
            lines.append(f"- {x}")
        lines.append("")
    if exec_sum.get("recommended_actions"):
        lines.append("### Recommended actions")
        lines.append("")
        for x in exec_sum["recommended_actions"]:
            lines.append(f"- {x}")
        lines.append("")
    lines.append(f"**Confidence:** `{exec_sum.get('confidence', 'medium')}`")
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

    tf = (signals or {}).get("top_failure") if isinstance(signals, dict) else None
    tt = (signals or {}).get("top_trigger") if isinstance(signals, dict) else None

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

    counts = (signals or {}).get("counts") if isinstance(signals, dict) else {}
    latency = (signals or {}).get("latency") if isinstance(signals, dict) else {}

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

    lines.append("## Artifacts")
    lines.append("")
    lines.append("| Artifact | File |")
    lines.append("|---|---|")
    lines.append(f"| Summary (json) | `{_artifact_name(summary_json_path)}` |")
    lines.append(f"| Summary (md) | `{_artifact_name(summary_md_path)}` |")
    lines.append(f"| Signals (json) | `{_artifact_name(signals_json_path)}` |")
    lines.append(f"| Signals (md) | `{_artifact_name(signals_md_path)}` |")
    lines.append("")

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


def _severity_badge(sev: str) -> str:
    sev_u = (sev or "NONE").upper()
    cls = "sev-none"
    if sev_u in ("LOW",):
        cls = "sev-low"
    elif sev_u in ("MEDIUM", "WARN"):
        cls = "sev-med"
    elif sev_u in ("HIGH", "CRITICAL", "ALERT"):
        cls = "sev-high"
    return f'<span class="badge {cls}">{_escape(sev_u)}</span>'


def render_report_html(
    *,
    kind: str,
    env: Optional[str],
    summary: Optional[Dict[str, Any]],
    signals: Optional[Dict[str, Any]],
    steps: Optional[List[Dict[str, Any]]],
    summary_json_path: Optional[str],
    summary_md_path: Optional[str],
    signals_json_path: Optional[str],
    signals_md_path: Optional[str],
    jsonl_path: Optional[str],
) -> str:
    kind = _get_kind(kind, summary, signals)
    sev = _get_severity(summary, signals)
    ok = _get_ok(kind, summary, signals)
    notes = _as_list((signals or {}).get("notes")) if isinstance(signals, dict) else []
    notes_s = ", ".join(sorted(str(n) for n in notes)) if notes else "none"

    exec_sum = _build_executive_summary(kind=kind, env=env, summary=summary, signals=signals)

    # Lightweight “timeline-ish” rows from JSONL/steps
    rows = steps or []
    # normalize to known fields for display
    def row_title(r: Dict[str, Any]) -> str:
        step = _first_nonempty(r.get("step"), r.get("name"), r.get("id"), "step")
        method = _first_nonempty(r.get("method"), "")
        url = _first_nonempty(r.get("url"), "")
        return f"{step} — {method} {url}".strip()

    def is_fail(r: Dict[str, Any]) -> bool:
        okv = r.get("ok")
        if isinstance(okv, bool):
            return not okv
        # fallback: error field or status >= 400
        if r.get("error"):
            return True
        sc = r.get("status_code")
        try:
            return sc is not None and int(sc) >= 400
        except Exception:
            return False

    total_steps = None
    fail_steps = None
    if isinstance(signals, dict):
        counts = signals.get("counts") or {}
        if isinstance(counts, dict):
            total_steps = counts.get("steps") or counts.get("total_payloads")
            fail_steps = counts.get("failures") or counts.get("error_count")
    if total_steps is None and rows:
        total_steps = len(rows)
    if fail_steps is None and rows:
        fail_steps = sum(1 for r in rows if is_fail(r))

    # Embed JSON payload for in-page filtering/search without external files
    embedded = {
        "kind": kind,
        "env": env,
        "severity": sev,
        "ok": ok,
        "notes": notes,
        "summary": summary or {},
        "signals": signals or {},
        "steps": rows,
        "artifacts": {
            "summary_json": _artifact_name(summary_json_path),
            "summary_md": _artifact_name(summary_md_path),
            "signals_json": _artifact_name(signals_json_path),
            "signals_md": _artifact_name(signals_md_path),
            "jsonl": _artifact_name(jsonl_path),
        },
    }

    # HTML (single file, offline)
    badge = _severity_badge(sev)
    ok_text = "PASS" if ok else "FAIL"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>CATE Report — {html.escape(sev.upper())}</title>
<style>
  :root {{
    --bg: #0f1116;
    --panel: #171a23;
    --panel2: #12141b;
    --border: #252837;
    --text: #f4f4f6;
    --muted: #a4abbf;
    --good: #3ddc97;
    --warn: #ffd166;
    --bad: #ff5c77;
    --link: #8ab4ff;
  }}
  body {{
    margin:0; background:var(--bg); color:var(--text);
    font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 22px; }}
  .top {{
    display:flex; gap:14px; align-items:flex-start; justify-content:space-between;
    padding:16px; background:var(--panel); border:1px solid var(--border); border-radius:16px;
    box-shadow: 0 8px 24px rgba(0,0,0,.25);
  }}
  .title h1 {{ margin:0; font-size:18px; letter-spacing:.2px; }}
  .sub {{ margin-top:6px; color:var(--muted); }}
  .badge {{
    display:inline-flex; align-items:center; gap:6px;
    padding:6px 10px; border-radius:999px; border:1px solid var(--border);
    font-weight:700; font-size:12px;
  }}
  .sev-none {{ color: var(--good); }}
  .sev-low  {{ color: var(--good); }}
  .sev-med  {{ color: var(--warn); }}
  .sev-high {{ color: var(--bad); }}
  .pill {{
    padding:6px 10px; border-radius:999px; border:1px solid var(--border);
    background:var(--panel2); color:var(--muted); font-size:12px;
  }}
  .grid {{ display:grid; grid-template-columns: 1.2fr .8fr; gap:16px; margin-top:16px; }}
  .card {{
    padding:16px; background:var(--panel); border:1px solid var(--border); border-radius:16px;
  }}
  .card h2 {{ margin:0 0 10px; font-size:15px; }}
  .card h3 {{ margin:14px 0 6px; font-size:13px; color:var(--muted); font-weight:700; text-transform:uppercase; letter-spacing:.08em; }}
  .kvs {{ display:grid; grid-template-columns: 140px 1fr; gap:8px 12px; }}
  .kvs div {{ padding:6px 0; border-bottom:1px dashed rgba(255,255,255,.06); }}
  .kvs .k {{ color:var(--muted); }}
  .kvs .v code {{ color: var(--text); background: rgba(255,255,255,.06); padding:2px 6px; border-radius:6px; }}
  a {{ color: var(--link); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .tools {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }}
  input[type="search"] {{
    flex:1; min-width:240px;
    padding:10px 12px; border-radius:12px; border:1px solid var(--border);
    background:var(--panel2); color:var(--text);
  }}
  button {{
    padding:10px 12px; border-radius:12px; border:1px solid var(--border);
    background:var(--panel2); color:var(--text); cursor:pointer;
  }}
  button.active {{ outline: 2px solid rgba(138,180,255,.35); }}
  .list {{ margin-top:10px; display:flex; flex-direction:column; gap:10px; }}
  details {{
    background:var(--panel2); border:1px solid var(--border); border-radius:14px; padding:10px 12px;
  }}
  summary {{
    cursor:pointer; list-style:none; display:flex; align-items:center; justify-content:space-between; gap:12px;
  }}
  summary::-webkit-details-marker {{ display:none; }}
  .row-title {{ font-weight:700; }}
  .row-meta {{ color:var(--muted); font-size:12px; text-align:right; }}
  .row-bad {{ color: var(--bad); }}
  .row-good {{ color: var(--good); }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
  pre {{
    margin:10px 0 0; padding:10px; border-radius:12px; border:1px solid var(--border);
    background: rgba(0,0,0,.25); overflow:auto; white-space:pre-wrap; word-break:break-word;
  }}
  .muted {{ color:var(--muted); }}
  .footer {{ margin-top:16px; color:var(--muted); font-size:12px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="title">
      <h1>CATE Report — {badge}</h1>
      <div class="sub">
        <span class="pill">Kind: <span class="mono">{_escape(kind)}</span></span>
        <span class="pill">Env: <span class="mono">{_escape(env or "—")}</span></span>
        <span class="pill">Result: <span class="mono">{_escape(ok_text)}</span></span>
        <span class="pill">Notes: <span class="mono">{_escape(notes_s)}</span></span>
      </div>
    </div>
    <div class="pill mono">CATE • offline HTML</div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Executive summary</h2>
      <p>{_escape(exec_sum["headline"])}</p>

      {"<h3>Key findings</h3><ul>" + "".join(f"<li>{_escape(x)}</li>" for x in exec_sum.get("key_findings", [])) + "</ul>" if exec_sum.get("key_findings") else ""}

      {"<h3>Recommended actions</h3><ul>" + "".join(f"<li>{_escape(x)}</li>" for x in exec_sum.get("recommended_actions", [])) + "</ul>" if exec_sum.get("recommended_actions") else ""}

      <p><strong>Confidence:</strong> <span class="mono">{_escape(exec_sum.get("confidence","medium"))}</span></p>
    </div>

    <div class="card">
      <h2>Artifacts</h2>
      <div class="kvs">
        <div class="k">Summary (json)</div><div class="v"><code>{_escape(_artifact_name(summary_json_path))}</code></div>
        <div class="k">Summary (md)</div><div class="v"><code>{_escape(_artifact_name(summary_md_path))}</code></div>
        <div class="k">Signals (json)</div><div class="v"><code>{_escape(_artifact_name(signals_json_path))}</code></div>
        <div class="k">Signals (md)</div><div class="v"><code>{_escape(_artifact_name(signals_md_path))}</code></div>
        <div class="k">JSONL</div><div class="v"><code>{_escape(_artifact_name(jsonl_path))}</code></div>
      </div>

      <h3>Totals</h3>
      <div class="kvs">
        <div class="k">Steps/Rows</div><div class="v"><code>{_escape(total_steps)}</code></div>
        <div class="k">Failures</div><div class="v"><code>{_escape(fail_steps)}</code></div>
      </div>

      <div class="footer">Generated by CATE</div>
    </div>
  </div>

  <div class="card" style="margin-top:16px;">
    <h2>Run details</h2>
    <div class="tools">
      <button id="btnAll" class="active" type="button">All</button>
      <button id="btnFail" type="button">Failures</button>
      <button id="btnOk" type="button">OK</button>
      <input id="search" type="search" placeholder="Search step, url, status, error..." />
      <span class="muted mono" id="count"></span>
    </div>

    <div class="list" id="list"></div>

    <div class="footer muted">
      Tip: click a row to expand. This file is self-contained and safe to share.
    </div>
  </div>
</div>

<script id="cate-data" type="application/json">
{json.dumps(embedded, ensure_ascii=False)}
</script>

<script>
(function() {{
  const data = JSON.parse(document.getElementById("cate-data").textContent);
  const list = document.getElementById("list");
  const search = document.getElementById("search");
  const count = document.getElementById("count");
  const btnAll = document.getElementById("btnAll");
  const btnFail = document.getElementById("btnFail");
  const btnOk = document.getElementById("btnOk");

  let mode = "all"; // all | fail | ok
  let q = "";

  function isFail(r) {{
    if (typeof r.ok === "boolean") return !r.ok;
    if (r.error) return true;
    const sc = r.status_code;
    if (typeof sc === "number") return sc >= 400;
    if (typeof sc === "string") {{
      const n = parseInt(sc, 10);
      if (!isNaN(n)) return n >= 400;
    }}
    return false;
  }}

  function rowText(r) {{
    const step = (r.step || r.name || r.id || "");
    const method = (r.method || "");
    const url = (r.url || "");
    const err = (r.error || "");
    const sc = (r.status_code == null ? "" : String(r.status_code));
    return (step + " " + method + " " + url + " " + err + " " + sc).toLowerCase();
  }}

  function esc(s) {{
    return String(s == null ? "" : s)
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;")
      .replaceAll('"',"&quot;");
  }}

  function render() {{
    list.innerHTML = "";
    const rows = Array.isArray(data.steps) ? data.steps : [];
    let shown = 0;

    for (const r of rows) {{
      const fail = isFail(r);
      if (mode === "fail" && !fail) continue;
      if (mode === "ok" && fail) continue;
      if (q && !rowText(r).includes(q)) continue;

      shown += 1;
      const step = (r.step || r.name || r.id || "step");
      const method = (r.method || "");
      const url = (r.url || "");
      const sc = (r.status_code == null ? "—" : String(r.status_code));
      const ms = (typeof r.elapsed_ms === "number" ? r.elapsed_ms.toFixed(1) : (r.elapsed_ms || "—"));
      const bytes = (r.bytes == null ? "—" : String(r.bytes));
      const err = (r.error || "");

      const title = step + " — " + method + " " + url;
      const meta = "status=" + sc + " • " + ms + " ms • " + bytes + " bytes" + (err ? " • error" : "");
      const cls = fail ? "row-bad" : "row-good";

      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.innerHTML =
        '<div class="row-title ' + cls + '">' + esc(title) + '</div>' +
        '<div class="row-meta mono">' + esc(meta) + '</div>';

      const pre = document.createElement("pre");
      pre.className = "mono";
      pre.innerHTML = esc(JSON.stringify(r, null, 2));

      details.appendChild(summary);
      details.appendChild(pre);
      list.appendChild(details);
    }}

    count.textContent = shown + " shown";
    if (shown === 0) {{
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "No matching rows.";
      list.appendChild(p);
    }}
  }}

  function setActive(btn) {{
    btnAll.classList.remove("active");
    btnFail.classList.remove("active");
    btnOk.classList.remove("active");
    btn.classList.add("active");
  }}

  btnAll.addEventListener("click", () => {{ mode = "all"; setActive(btnAll); render(); }});
  btnFail.addEventListener("click", () => {{ mode = "fail"; setActive(btnFail); render(); }});
  btnOk.addEventListener("click", () => {{ mode = "ok"; setActive(btnOk); render(); }});

  search.addEventListener("input", (e) => {{
    q = (e.target.value || "").toLowerCase().trim();
    render();
  }});

  render();
}})();
</script>
</body>
</html>
"""


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

    effective_kind = _get_kind(kind, summary, signals)

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


def write_report_html(
    *,
    output_prefix: str,
    env: Optional[str] = None,
    kind: Optional[str] = None,
    summary_json_path: Optional[str] = None,
    summary_md_path: Optional[str] = None,
    signals_json_path: Optional[str] = None,
    signals_md_path: Optional[str] = None,
    jsonl_path: Optional[str] = None,
    jsonl_limit: int = 2000,
) -> str:
    """
    Writes <prefix>.report.html next to other artifacts and returns the path.
    Best-effort: if it can't read artifacts, it still writes a shell.
    """
    out = _normalize_output_prefix(output_prefix)
    report_path = out.with_suffix(".report.html")

    summary = _safe_read_json(Path(summary_json_path)) if summary_json_path else None
    signals = _safe_read_json(Path(signals_json_path)) if signals_json_path else None
    steps = _safe_read_jsonl(Path(jsonl_path), limit=jsonl_limit) if jsonl_path else None

    effective_kind = _get_kind(kind, summary, signals)

    html_text = render_report_html(
        kind=effective_kind,
        env=env,
        summary=summary,
        signals=signals,
        steps=steps,
        summary_json_path=summary_json_path,
        summary_md_path=summary_md_path,
        signals_json_path=signals_json_path,
        signals_md_path=signals_md_path,
        jsonl_path=jsonl_path,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html_text, encoding="utf-8")
    return str(report_path)
