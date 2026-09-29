# core/report.py
from __future__ import annotations
import os
import html
import json
from typing import Dict, Any, Optional


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>MARCS Report — {event_id}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial; margin: 24px; color: #111; }}
    header {{ display:flex; align-items:center; gap:16px; margin-bottom:20px; }}
    .logo {{ width:56px; height:56px; border-radius:8px; background:#0b67ff; color:#fff; display:flex; align-items:center; justify-content:center; font-weight:700; }}
    h1 {{ margin:0; font-size:20px; }}
    .meta {{ color:#555; font-size:13px; }}
    .card {{ border:1px solid #eee; padding:12px; border-radius:8px; margin-bottom:12px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }}
    pre.diff {{ white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, "Courier New", monospace; font-size:13px; background:#f8f9fb; padding:12px; border-radius:6px; overflow:auto; }}
    .row {{ display:flex; gap:12px; align-items:flex-start; }}
    .col-2 {{ flex:1 1 50%; min-width: 240px; }}
    .badge {{ display:inline-block; padding:4px 8px; border-radius:999px; background:#eef5ff; color:#0b67ff; font-weight:600; font-size:12px; }}
    ul {{ margin:6px 0 6px 20px; }}
    .small {{ color:#666; font-size:13px; }}
    .warn {{ color:#8a2b2b; font-weight:700; }}
  </style>
</head>
<body>
  <header>
    <div class="logo">M</div>
    <div>
      <h1>MARCS Report — {event_id}</h1>
      <div class="meta">Generated file: <strong>{report_path}</strong></div>
    </div>
  </header>

  <div class="card">
    <div class="row">
      <div class="col-2"><strong>Event ID</strong><div class="small">{event_id}</div></div>
      <div class="col-2"><strong>Repository</strong><div class="small">{repo_root}</div></div>
    </div>
  </div>

  <div class="card">
    <strong>Summary</strong>
    <div class="small">Patches: <span class="badge">{patch_count}</span>
      Applied: <span class="badge">{applied_count}</span>
      Dropped/Conflicts: <span class="badge">{dropped_count}</span>
      Dry run: <span class="badge">{dry_run}</span>
    </div>
  </div>

  <div class="card">
    <strong>Findings</strong>
    {findings_block}
  </div>

  <div class="card">
    <strong>Patch previews & diffs</strong>
    {patches_block}
  </div>

  <div class="card">
    <strong>Apply Results</strong>
    <div class="small">
      Journal: {journal_path}<br/>
      Backups: {backups_block}
    </div>
    <pre class="diff">{apply_json}</pre>
  </div>

  <div class="card">
    <strong>Safety warnings</strong>
    {safety_block}
  </div>

  <footer class="small" style="margin-top:18px;color:#888;">
    MARCS — Multi-Agent Code Review System. Report generated automatically.
  </footer>
</body>
</html>
"""


def _render_findings(findings: Optional[list]) -> str:
    if not findings:
        return "<div class='small'>No findings.</div>"
    out = ["<ul>"]
    for f in findings:
        out.append(f"<li>{html.escape(str(f))}</li>")
    out.append("</ul>")
    return "\n".join(out)


def _render_patches(suggested_patches: Dict[str, str]) -> str:
    if not suggested_patches:
        return "<div class='small'>No suggested patches.</div>"
    parts = []
    for pid, patch in suggested_patches.items():
        parts.append(f"<div style='margin-bottom:10px;'><strong>{html.escape(str(pid))}</strong><pre class='diff'>{html.escape(patch)}</pre></div>")
    return "\n".join(parts)


def _render_backups(backups: Dict[str, str]) -> str:
    if not backups:
        return "None"
    parts = []
    for f, p in backups.items():
        parts.append(f"{html.escape(f)} → {html.escape(p)}")
    return "<br/>".join(parts)


def _render_safety(safety_map: Optional[Dict[str, list]]) -> str:
    if not safety_map:
        return "<div class='small'>No safety warnings detected.</div>"
    parts = ["<ul>"]
    for f, warnings in safety_map.items():
        parts.append(f"<li><span class='warn'>{html.escape(f)}</span>: {html.escape(', '.join(warnings))}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def generate_html_report(event_id: str,
                         review: Dict[str, Any],
                         apply_result: Optional[Dict[str, Any]],
                         artifact_dir: str) -> str:
    """
    Generates a static HTML report under artifact_dir/<event_id>/report.html.
    Returns the path to the generated report.
    """
    out_dir = os.path.join(artifact_dir, event_id)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "report.html")

    repo_root = review.get("_repo_root") or (apply_result.get("repo") if apply_result else "") or ""

    suggested = review.get("suggested_patches") or {}
    findings = review.get("findings") or []

    # basic counts
    applied = apply_result.get("applied") if apply_result else {}
    dropped = apply_result.get("dropped") if apply_result else {}
    backups = apply_result.get("backups") if apply_result else {}
    journal = apply_result.get("journal") if apply_result else ""

    safety_map = review.get("_safety_warnings") or (apply_result.get("safety_warnings") if apply_result else {})

    html_content = HTML_TEMPLATE.format(
        event_id=html.escape(event_id),
        report_path=html.escape(report_path),
        repo_root=html.escape(str(repo_root)),
        patch_count=len(suggested),
        applied_count=len(applied) if applied else 0,
        dropped_count=len(dropped) if dropped else 0,
        dry_run=str(bool(apply_result.get("dry_run")) if apply_result else False).lower(),
        findings_block=_render_findings(findings),
        patches_block=_render_patches(suggested),
        journal_path=html.escape(str(journal) if journal else "n/a"),
        backups_block=_render_backups(backups or {}),
        apply_json=html.escape(json.dumps(apply_result or {}, indent=2)),
        safety_block=_render_safety(safety_map or {}),
    )

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    return report_path