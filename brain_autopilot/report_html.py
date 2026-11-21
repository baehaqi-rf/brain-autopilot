"""Zero-dependency self-contained HTML report generator (`brain-autopilot report-html`).

Converts a Brain Autopilot run directory (`trace*.jsonl`, `trace*_summary.txt`,
`report.json`, and captured PNG screenshots) into a single portable dark-mode HTML
file suitable for browser review or CI artifact upload.
"""

from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path
from typing import Any

from . import __version__


def _natural_trace_key(path: Path) -> tuple[int, str]:
    m = re.search(r"(\d+)", path.stem)
    return (int(m.group(1)) if m else 0, path.name)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_trace(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return rows


def generate_html(run_dir: str | Path, out_path: str | Path | None = None) -> str:
    """Generate a self-contained HTML report for `run_dir` and return its output path."""
    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Run directory not found: {root}")

    report = _load_json(root / "report.json") or {}
    traces = sorted(root.glob("trace*.jsonl"), key=_natural_trace_key)
    trace_rows = _load_trace(traces[-1]) if traces else []
    summaries = sorted(root.glob("trace*_summary.txt"), key=_natural_trace_key)
    summary_text = summaries[-1].read_text(encoding="utf-8", errors="replace") if summaries else ""
    pngs = sorted(root.glob("*.png"))[:12]

    decisions = [r for r in trace_rows if r.get("kind") == "decision" or r.get("type") == "decision"]
    findings: list[dict[str, Any]] = []
    seen_sigs: set[str] = set()
    for row in [*trace_rows, report]:
        if not isinstance(row, dict):
            continue
        for f in row.get("findings") or []:
            if isinstance(f, dict):
                sig = str(f.get("signature") or f.get("message") or "")
                if sig and sig not in seen_sigs:
                    seen_sigs.add(sig)
                    findings.append(f)
            elif isinstance(f, str) and f.strip():
                msg = f.strip()
                sig = f"report:{msg}"
                if sig not in seen_sigs:
                    seen_sigs.add(sig)
                    findings.append({"severity": "info", "detector": "report", "signature": sig, "message": msg})

    outcome = str(report.get("outcome") or ("COMPLETED" if trace_rows else "UNKNOWN"))
    outcome_up = outcome.upper()
    outcome_cls = (
        "sev-error"
        if outcome_up in ("DENIED", "FAILED", "NOT_ACHIEVED")
        else ("sev-warning" if outcome_up in ("INCONCLUSIVE", "STALLED", "UNKNOWN") else "sev-ok")
    )
    title = html.escape(f"Brain Autopilot v{__version__} Report - {root.name}")
    outcome_badge = html.escape(outcome)
    summary_line = html.escape(str(report.get("summary") or "Autonomous verification run report"))

    finding_rows_html = ""
    if findings:
        for f in findings:
            sev = html.escape(str(f.get("severity") or "info"))
            det = html.escape(str(f.get("detector") or "detector"))
            msg = html.escape(str(f.get("message") or f.get("signature") or ""))
            finding_rows_html += (
                f"<tr><td><span class='badge sev-{sev}'>{sev}</span></td>"
                f"<td><code>{det}</code></td><td>{msg}</td></tr>"
            )
    else:
        finding_rows_html = "<tr><td colspan='3' class='muted'>No detector findings recorded.</td></tr>"

    decision_rows_html = ""
    if decisions:
        for idx, d in enumerate(decisions, 1):
            t_val = html.escape(str(d.get("t", "")))
            opt = html.escape(str(d.get("chosen") or d.get("option") or ""))
            step = html.escape(str(d.get("step") or "-"))
            ok = d.get("ok", True)
            eff = d.get("effect")
            sc = d.get("state_changed")
            eff_str = html.escape("—" if eff is None else str(eff))
            sc_str = html.escape("—" if sc is None else ("yes" if sc else "no-op"))
            why_str = html.escape(str(d.get("why") or "")[:90])
            status_lbl = "PASS" if ok else "FAIL"
            cls = "sev-ok" if ok else "sev-error"
            decision_rows_html += (
                f"<tr><td>{idx}</td><td>{t_val}s</td><td><code>{step}</code></td>"
                f"<td><code>{opt}</code></td><td>{eff_str}</td><td>{sc_str}</td>"
                f"<td><span class='badge {cls}'>{status_lbl}</span> <span class='muted'>{why_str}</span></td></tr>"
            )
    else:
        decision_rows_html = "<tr><td colspan='7' class='muted'>No trace decisions recorded.</td></tr>"

    gallery_html = ""
    for png in pngs:
        try:
            b64 = base64.b64encode(png.read_bytes()).decode("ascii")
            label = html.escape(png.name)
            gallery_html += (
                f"<figure class='shot'><img src='data:image/png;base64,{b64}' alt='{label}'/>"
                f"<figcaption>{label}</figcaption></figure>"
            )
        except OSError:
            continue
    if not gallery_html:
        gallery_html = "<p class='muted'>No PNG screenshots found in run directory.</p>"

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --border:#30363d; --fg:#e6edf3; --muted:#8b949e; --accent:#58a6ff; }}
  body {{ margin:0; font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--fg); line-height:1.5; }}
  main {{ max-width:1080px; margin:0 auto; padding:28px 20px 60px; }}
  header {{ display:flex; justify-content:space-between; align-items:center; gap:16px; border-bottom:1px solid var(--border); padding-bottom:16px; margin-bottom:24px; }}
  h1 {{ margin:0; font-size:1.45rem; }}
  h2 {{ margin:28px 0 12px; font-size:1.1rem; color:var(--accent); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
  .card .k {{ font-size:0.78rem; color:var(--muted); text-transform:uppercase; letter-spacing:0.04em; }}
  .card .v {{ font-size:1.4rem; font-weight:700; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:10px; overflow:hidden; }}
  th, td {{ padding:10px 12px; border-bottom:1px solid var(--border); text-align:left; font-size:0.9rem; }}
  th {{ color:var(--muted); font-weight:600; background:#1c2128; }}
  pre {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px; overflow-x:auto; font-size:0.84rem; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:0.75rem; font-weight:600; }}
  .sev-error {{ background:#490202; color:#ff7b72; }}
  .sev-warning {{ background:#3b2300; color:#f2cc60; }}
  .sev-ok, .sev-info {{ background:#0f2f1f; color:#56d364; }}
  .muted {{ color:var(--muted); }}
  .gallery {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:14px; }}
  .shot {{ margin:0; background:var(--card); border:1px solid var(--border); border-radius:10px; padding:10px; }}
  .shot img {{ width:100%; height:auto; border-radius:6px; display:block; }}
  .shot figcaption {{ margin-top:8px; font-size:0.8rem; color:var(--muted); }}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>{title}</h1>
      <div class="muted">{summary_line}</div>
    </div>
    <span class="badge {outcome_cls}">{outcome_badge}</span>
  </header>

  <section class="grid">
    <div class="card"><div class="k">Decisions</div><div class="v">{len(decisions)}</div></div>
    <div class="card"><div class="k">Trace Events</div><div class="v">{len(trace_rows)}</div></div>
    <div class="card"><div class="k">Detector Findings</div><div class="v">{len(findings)}</div></div>
    <div class="card"><div class="k">Screenshots</div><div class="v">{len(pngs)}</div></div>
  </section>

  <h2>Detector Findings</h2>
  <table>
    <thead><tr><th>Severity</th><th>Detector</th><th>Message</th></tr></thead>
    <tbody>{finding_rows_html}</tbody>
  </table>

  <h2>Decision Timeline</h2>
  <table>
    <thead><tr><th>#</th><th>Time</th><th>Step</th><th>Option</th><th>Effect</th><th>State Δ</th><th>Status</th></tr></thead>
    <tbody>{decision_rows_html}</tbody>
  </table>

  <h2>Trace Summary</h2>
  <pre>{html.escape(summary_text or "No trace summary file available.")}</pre>

  <h2>Captured Frames</h2>
  <div class="gallery">{gallery_html}</div>
</main>
</body>
</html>
"""
    target = Path(out_path) if out_path else (root / "report.html")
    target.write_text(html_doc, encoding="utf-8")
    return str(target)
