"""services/patch_selector.py

Responsible for deduping, scoring and ranking patches produced by agents.
This is a deterministic, rule-based Phase-0 implementation used as a
fallback/routing layer before Phase-1 LLM-based re-ranking.

Main API:
- apply_patch_ranking(review) -> returns selected_patches dict (patch_id->text)
- score_patch(finding, patch_text) -> float in [0.0, 1.0]
- dedupe_similar_patches(patch_map) -> reduced patch_map

"""
from __future__ import annotations
from typing import Dict, Any, List, Tuple
import math
import re
import hashlib
from collections import defaultdict
from agents.results import SynthesizedReview, AgentFinding
from core.logging import get_logger

logger = get_logger("services.patch_selector")


# --- Utility similarity functions (lightweight, no extra deps) ---

def _tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    # split on non-word, keep tokens length>1
    toks = re.findall(r"\w{2,}", text)
    return toks


def jaccard(a: str, b: str) -> float:
    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta and not tb:
        return 1.0 if a == b else 0.0
    inter = ta.intersection(tb)
    uni = ta.union(tb)
    return len(inter) / len(uni) if uni else 0.0


def normalized_length_score(text: str, max_len: int = 600) -> float:
    l = len(text or "")
    return 1.0 - min(l, max_len) / max_len


# --- Core heuristics for scoring ---

def score_patch(finding: AgentFinding, patch_text: str) -> float:
    """Compute a simple heuristic quality score in range [0,1].

    Combines multiple small signals:
      - exact/substring match with finding.message
      - rule/metadata keyword match
      - category-specific boosts (security/style)
      - presence of code-like fragments
      - patch conciseness
      - patch uniqueness (short-term)
    """
    if not patch_text:
        return 0.0

    score = 0.0
    text = patch_text.strip()
    lower = text.lower()

    # 1) direct message substring match (strong)
    msg = (finding.message or "").lower()
    if msg and msg in lower:
        score += 0.30
    elif msg and msg[:40] and msg[:40] in lower:
        score += 0.18

    # 2) evidence/rule match
    rule = (finding.metadata or {}).get("rule") or (finding.metadata or {}).get("pattern")
    if rule and str(rule).lower() in lower:
        score += 0.18

    # 3) category signals
    if finding.category == "security":
        if any(k in lower for k in ("secret", "vault", "env", "rotate", "token")):
            score += 0.16
    if finding.category == "style":
        if any(k in lower for k in ("line", "wrap", "docstring", "format")):
            score += 0.08

    # 4) code-like fragment detection (```, def, =, ;, import)
    code_like = bool(re.search(r"\b(def |class |import |return |=|;|```|\{\{|\}\})", text))
    if code_like:
        score += 0.12

    # 5) conciseness / length friendly
    score += 0.06 * normalized_length_score(text)

    # clamp
    return max(0.0, min(1.0, score))


def dedupe_similar_patches(patches: Dict[str, str], threshold: float = 0.86) -> Dict[str, str]:
    """Remove near-duplicate patches by Jaccard similarity on tokens.

    Keeps the patch with longest text among duplicates (heuristic: more complete).
    """
    keys = list(patches.keys())
    removed = set()
    for i in range(len(keys)):
        if keys[i] in removed:
            continue
        for j in range(i + 1, len(keys)):
            k1 = keys[i]
            k2 = keys[j]
            if k2 in removed:
                continue
            s = jaccard(patches[k1], patches[k2])
            if s >= threshold:
                # choose winner (longer text assumed more informative)
                if len(patches[k1]) >= len(patches[k2]):
                    removed.add(k2)
                else:
                    removed.add(k1)
    out = {k: v for k, v in patches.items() if k not in removed}
    if removed:
        logger.info("patch_deduped", removed=list(removed))
    return out


# --- Main orchestrator ---
def rank_patches_for_finding(finding: AgentFinding, patches: Dict[str, str]) -> List[Tuple[str, float]]:
    scored = []
    for pid, text in patches.items():
        sc = score_patch(finding, text)
        scored.append((pid, sc))
    # sort descending
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def apply_patch_ranking(review: SynthesizedReview, dedupe_threshold: float = 0.86) -> Dict[str, str]:
    """Compute patch scores + select top patch per finding.

    Effects:
      - normalizes patches via dedupe
      - populates review.metadata['patch_scores'] a mapping finding_id -> list of {patch_id,score}
      - populates review.metadata['selected_patch'] mapping finding_id -> patch_id (top)
      - returns selected_patches dict (patch_id->text) which is convenient to set as review.suggested_patches when linking
    """
    patches = getattr(review, "suggested_patches", {}) or {}
    if not patches:
        review.metadata.setdefault("patch_scores", {})
        review.metadata.setdefault("selected_patch", {})
        return {}

    # 1) dedupe near-duplicates first
    patches_clean = dedupe_similar_patches(patches, threshold=dedupe_threshold)

    # 2) build mapping from finding -> scored list
    patch_scores = {}
    selected = {}

    for f in (review.findings or []):
        scored = rank_patches_for_finding(f, patches_clean)
        # filter low-scoring patches (tiny threshold)
        scored_filtered = [(pid, sc) for pid, sc in scored if sc > 0.0]
        # if none matched, still include generic ranking for visibility
        if not scored_filtered:
            # score all patches very low but keep ordering
            scored_filtered = [(pid, sc) for pid, sc in scored]
        patch_scores[f.id] = [{"patch_id": pid, "score": float(sc)} for pid, sc in scored_filtered]
        if patch_scores[f.id]:
            selected[f.id] = patch_scores[f.id][0]["patch_id"]

    # store in metadata
    review.metadata["patch_scores"] = patch_scores
    review.metadata["selected_patch"] = selected

    # return dict of selected patch_id -> patch_text (unique set)
    chosen = {}
    for fid, pid in selected.items():
        if pid in patches_clean:
            chosen[pid] = patches_clean[pid]
    return chosen


# Expose a simple API wrapper for worker
def annotate_and_select(review: SynthesizedReview) -> None:
    """In-place annotate review and set `review.suggested_patches` to the selected patch dict.

    This function is convenient for the worker pipeline: call it BEFORE link_patches()
    so that only selected patches get linked.
    """
    selected = apply_patch_ranking(review)
    # write selected patches into top-level suggested_patches_selected for debugging
    review.metadata["selected_patches_dict"] = selected
    # replace top-level suggested_patches to selected so downstream linking uses them
    review.suggested_patches = selected


# simple CLI test helper
if __name__ == "__main__":
    import json
    from agents.results import SynthesizedReview, AgentFinding
    f = AgentFinding(id="f1", severity="low", message="line too long", category="style")
    review = SynthesizedReview(event_id="x", repo="me/repo", findings=[f], suggested_patches={"p1": "# shorten long line\n...", "p2": "# docstring advice\n..."})
    apply_patch_ranking(review)
    print(json.dumps(review.metadata, indent=2))