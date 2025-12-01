"""
services/patch_eval.py

Utilities to evaluate, inspect and preview patch scoring & selection.

Primary helpers:
 - extract_features_for_patch(review, patch_id, patch_text, finding=None) -> dict
 - score_patch_vs_finding(review, finding, patch_id, patch_text) -> PatchScore
 - preview_selector_pipeline_v2(review) -> dict report (scores, selected)

Also includes a small CLI to load a review JSON file and print a report.
"""

from __future__ import annotations

import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import sys
from typing import Optional, Dict, Any, List
from core.logging import get_logger

from agents.results import SynthesizedReview, AgentFinding
from services.patch_scorer import (
    PatchScore,
    score_patch_for_finding,
    score_all_patches,
    _rule_match_score,
    _message_substring_score,
    _length_score,
    _novelty_score,
    _security_sensitivity_score,
    _syntactic_quality_score,
)
from services.patch_selector_pro import select_patches_for_review

logger = get_logger("services.patch_eval")


def extract_features_for_patch(
    review: SynthesizedReview,
    patch_id: str,
    patch_text: str,
    finding: Optional[AgentFinding] = None,
) -> Dict[str, Any]:
    """
    Return a feature dict for the given patch text.
    If `finding` is provided, compute matching features against that finding,
    otherwise compute generic features that don't require a finding.
    """
    features: Dict[str, Any] = {}
    features["patch_id"] = str(patch_id)
    features["length"] = len(patch_text or "")
    features["has_newline"] = "\n" in (patch_text or "")
    features["token_count"] = len([t for t in (patch_text or "").split() if t.strip()])

    # finding-specific features (if available)
    if finding is not None:
        features["rule_match"] = float(_rule_match_score(patch_text, finding))
        features["message_overlap"] = float(_message_substring_score(patch_text, finding))
        features["length_score"] = float(_length_score(patch_text))
        features["novelty"] = float(_novelty_score(patch_text, review))
        features["security_sensitive"] = float(_security_sensitivity_score(patch_text, finding))
        features["syntactic_quality"] = float(_syntactic_quality_score(patch_text))
    else:
        # approximate novelty only
        features["novelty"] = float(_novelty_score(patch_text, review))
        features["syntactic_quality"] = float(_syntactic_quality_score(patch_text))
    return features


def score_patch_vs_finding(
    review: SynthesizedReview,
    finding: AgentFinding,
    patch_id: str,
    patch_text: str
) -> PatchScore:
    """
    Wrapper around score_patch_for_finding to produce a PatchScore for a single
    (finding, patch) tuple. Returns the PatchScore dataclass.
    """
    return score_patch_for_finding(review, finding, str(patch_id), patch_text)


def preview_selector_pipeline_v2(review: SynthesizedReview) -> Dict[str, Any]:
    """
    Run a preview of the v2 selection pipeline:
      - score all patches
      - return detailed breakdown of scores
      - run selection policy (select_patches_for_review) and return selected map

    Returns:
      {
         "all_scores": [ {patchscore dict}, ... ],
         "grouped_by_finding": { finding_id: [patchscore dict, ...] },
         "selected": review.metadata["selected_patches"] (after selection),
         "selected_map": { patch_id: patch_text }  # convenience
      }
    """
    report: Dict[str, Any] = {}
    # score all patches
    all_scores: List[PatchScore] = score_all_patches(review)
    report["all_scores"] = [ps.to_dict() for ps in all_scores]

    # group by finding for debugging
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ps in all_scores:
        grouped.setdefault(ps.finding_id, []).append(ps.to_dict())
    report["grouped_by_finding"] = grouped

    # run selection (this mutates review.metadata["selected_patches"])
    select_patches_for_review(review)
    report["selected"] = review.metadata.get("selected_patches", {})

    # reconstruct selected_map (patch_id -> patch_text) from review.suggested_patches
    selected_map: Dict[str, str] = {}
    # If suggested_patches already contains the raw text, use it
    sp = getattr(review, "suggested_patches", {}) or {}
    for fid, items in report["selected"].items():
        for info in items:
            pid = info.get("patch_id")
            if pid in sp:
                selected_map[pid] = sp[pid]
    report["selected_map"] = selected_map

    return report


# ----------------------------
# CLI for local debugging
# ----------------------------
def _load_review_from_file(path: str) -> SynthesizedReview:
    with open(path, "r", encoding="utf-8") as fh:
        j = json.load(fh)
    # try to construct pydantic model
    return SynthesizedReview(**j)


def _print_report(report: Dict[str, Any]) -> None:
    print("=== PATCH EVAL PREVIEW ===")
    print(f"Total scored patches: {len(report.get('all_scores', []))}")
    print("")
    print("Top scores (global):")
    for item in sorted(report.get("all_scores", []), key=lambda x: x.get("score", 0.0), reverse=True)[:10]:
        print(f"  - {item['patch_id']} -> score={item['score']:.4f} finding={item['finding_id']}")

    print("")
    print("Selected patches (per finding):")
    for fid, items in (report.get("selected") or {}).items():
        print(f" Finding {fid}:")
        for it in items:
            print(f"    - {it['patch_id']} score={it['score']:.4f}")

    print("")
    print("Selected map keys:", list(report.get("selected_map", {}).keys()))
    print("=== END ===")


def run_cli_load_and_preview(path: str) -> int:
    try:
        review = _load_review_from_file(path)
    except Exception as e:
        logger.error("patch_eval_load_failed", path=path, error=str(e))
        print(f"Failed to load review from {path}: {e}", file=sys.stderr)
        return 2

    report = preview_selector_pipeline_v2(review)
    _print_report(report)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m services.patch_eval <path-to-review-json>", file=sys.stderr)
        sys.exit(2)
    path = sys.argv[1]
    sys.exit(run_cli_load_and_preview(path))