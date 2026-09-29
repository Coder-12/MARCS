#!/usr/bin/env python3
# scripts/demo_orchestrator.py


from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
from typing import Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

from orchestration.orchestrator import DemoOrchestrator
from core.logging import get_logger

from core.env_check import warn_env
warn_env()   # No fail-fast, but warns user

logger = get_logger("demo-orch") if hasattr(__import__("core.logging"), "get_logger") else None

def parse_args():
    p = argparse.ArgumentParser(description="Demo Orchestrator - review, preview, optionally apply patches")
    p.add_argument("--event-id", required=True, help="Unique event id (used for journals/artifacts)")
    p.add_argument("--repo-root", required=True, help="Path to repo root where patches will be applied")
    p.add_argument("--review-json", help="Path to precomputed review JSON (skip running reviewer)")
    p.add_argument("--yes", action="store_true", help="Auto-confirm apply (non-interactive)")
    p.add_argument("--dry-run", action="store_true", help="Do not actually write files — only dry-run apply (default true if not --yes)")
    p.add_argument("--output-json", help="Write final summary JSON to this path")
    p.add_argument("--preview-only", action="store_true", help="Only show patch preview and exit")
    p.add_argument("--safety-fail", action="store_true", help="Fail apply if any safety warning is detected")
    return p.parse_args()


async def main_async(args):
    orch = DemoOrchestrator()
    event = {"event_id": args.event_id, "repo_root": args.repo_root}

    # Load review if provided
    review = None
    if args.review_json:
        with open(args.review_json, "r", encoding="utf-8") as fh:
            review = json.load(fh)
    else:
        # Try to call reviewer (async)
        try:
            review = await orch.run_review(event)
        except Exception as e:
            print(f"ERROR: running reviewer: {e}", file=sys.stderr)
            raise

    patches = orch._summarize_patches(review)
    orch.log_event("preview_render_started", event_id=args.event_id)
    orch.validate_patch_sizes(patches)

    # Collect safety warnings for CLI display
    safety_map = {}
    for fname, ptxt in patches:
        warnings = orch.run_safety_checks(fname, ptxt)
        if warnings:
            safety_map[fname] = warnings

    orch._latest_safety_map = safety_map

    # Show warnings (human-readable)
    if safety_map:
        print("\n⚠️  SAFETY WARNINGS DETECTED:")
        for fname, wlist in safety_map.items():
            print(f"  - {fname}: {', '.join(wlist)}")
        print("--------------------------------------------------")
        print("These patches may be risky. Review them carefully.")

    print("== Review summary ==")
    print(json.dumps({"findings": review.get("findings", []), "patch_count": len(patches)}, indent=2))
    print("\n== Patch previews ==")
    print(orch.render_patch_preview(patches))

    if args.preview_only:
        print("Preview-only mode, exiting.")
        orch.log_event("preview_render_finished", event_id=args.event_id)
        return {"status": "previewed", "patch_count": len(patches)}


    if not patches:
        print("No patches suggested. Exiting.")
        return {"status": "no_patches"}

    if args.safety_fail and safety_map:
        print("❌ Safety-fail active — risky patches detected. Aborting apply.")
        print("status: blocked_by_safety")
        orch.log_event("safety_fail_block", event_id=args.event_id, warnings=safety_map)
        return {"status": "blocked_by_safety", "warnings": safety_map}

    # Interactive selection unless auto-confirmed
    selected_map = {}
    if args.yes:
        selected_map = {pid: txt for pid, txt in patches}
        dry_run_mode = args.dry_run or False  # if user specified --dry-run honor it; otherwise allow apply
    else:
        # default: dry-run True unless user passed --yes without --dry-run
        dry_run_mode = args.dry_run or True
        selected_map = orch.interactive_choose(patches)

    print("Selection complete. Selected patches:", list(selected_map.keys()))
    orch.log_event("apply_started", event_id=args.event_id, selected=list(selected_map.keys()))
    # apply
    apply_res = orch.apply_selected(args.event_id, args.repo_root, selected_map, dry_run=dry_run_mode)

    # attach safety warnings into apply_result for artifact storage
    apply_res["safety_warnings"] = safety_map

    orch.log_event("apply_finished", event_id=args.event_id)

    # orch._latest_safety_map = safety_map

    orch.log_event("artifact_write_started", event_id=args.event_id)
    # write artifacts
    artifact_paths = orch.write_artifacts(args.event_id, review, apply_result=apply_res)
    orch.log_event("artifact_write_finished", event_id=args.event_id, paths=artifact_paths)

    summary = {
        "event_id": args.event_id,
        "selected": list(selected_map.keys()),
        "apply_result": apply_res,
        "artifacts": artifact_paths,
        "dry_run": dry_run_mode
    }

    # output as pretty JSON if asked
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)

    print("Orchestrator finished. Summary:")
    print(json.dumps(summary, indent=2))

    orch.log_event(
        "session_summary",
        event_id=args.event_id,
        patch_count=len(patches),
        selected=list(selected_map.keys()),
        dry_run=dry_run_mode,
        apply_result=apply_res,
        artifacts=artifact_paths
    )
    return summary


def main():
    args = parse_args()
    try:
        res = asyncio.run(main_async(args))
        # exit code 0 for success
        sys.exit(0)
    except Exception as e:
        print("Orchestrator failed:", str(e), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()