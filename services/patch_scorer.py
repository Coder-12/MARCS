# services/patch_scorer.py
from __future__ import annotations
import re
import math
import json
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from core.logging import get_logger
from agents.results import AgentFinding, SynthesizedReview

logger = get_logger("services.patch_scorer")


@dataclass
class PatchScore:
    finding_id: str
    patch_id: str
    score: float
    breakdown: Dict[str, float]
    reason: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d["score"] = float(d["score"])
        return d


# Helper heuristics
def _rule_match_score(patch_text: str, finding: AgentFinding) -> float:
    """
    Higher if the patch text directly mentions the rule or metadata.rule.
    Returns in range [0.0, 1.0]
    """
    if not patch_text:
        return 0.0
    patch = patch_text.lower()
    rule = (finding.metadata or {}).get("rule")
    score = 0.0
    if rule:
        if str(rule).lower() in patch:
            score = 1.0
        else:
            # partial fuzzy: split tokens from rule
            tokens = re.split(r"\W+", str(rule).lower())
            matched = sum(1 for t in tokens if t and t in patch)
            if tokens:
                score = min(1.0, matched / max(1, len(tokens)))
    return float(score)


def _message_substring_score(patch_text: str, finding: AgentFinding) -> float:
    """
    If patch contains parts of the finding message, give a boost.
    Normalized by overlap length / message length.
    """
    if not patch_text or not finding.message:
        return 0.0
    p = patch_text.lower()
    msg = finding.message.lower().strip()
    # look for longest common substring heuristic (bounded)
    max_len = 0
    # O(n^2) small cap
    cap = min(120, len(msg))
    for L in range(cap, 3, -1):
        if msg[:L] in p or msg[-L:] in p:
            max_len = L
            break
    if max_len == 0:
        # fallback token overlap
        msg_tokens = set(re.findall(r"\w{3,}", msg))
        p_tokens = set(re.findall(r"\w{3,}", p))
        if not msg_tokens:
            return 0.0
        overlap = len(msg_tokens & p_tokens)
        return min(1.0, overlap / max(1, len(msg_tokens))) * 0.6
    return min(1.0, max_len / len(msg)) * 0.9


def _length_score(patch_text: str) -> float:
    """
    Penalize extremely short patches (no content) and extremely long (probably dump).
    Prefer concise useful patches in mid-range.
    Map lengths to [0,1].
    """
    if not patch_text:
        return 0.0
    ln = len(patch_text)
    # desirable window [20, 800] characters
    if ln < 20:
        return ln / 20.0 * 0.3
    if ln <= 200:
        # peak at 100-200
        return 0.9
    if ln <= 800:
        return 0.9 - ((ln - 200) / 600) * 0.5  # degrade to ~0.4
    return 0.2


def _novelty_score(patch_text: str, review: SynthesizedReview) -> float:
    """
    Boost patches that are not already present in suggested_patches values.
    If identical string exists anywhere, return 0.0 novelty.
    """
    existing = list((review.suggested_patches or {}).values())
    if not existing:
        return 1.0
    for ex in existing:
        if not ex:
            continue
        if ex.strip() == patch_text.strip():
            return 0.0
    # small heuristic for near-duplicates
    lower_patch = re.sub(r"\s+", " ", patch_text.strip()).lower()
    for ex in existing:
        exn = re.sub(r"\s+", " ", (ex or "").strip()).lower()
        # high Jaccard-ish token overlap
        a = set(re.findall(r"\w+", lower_patch))
        b = set(re.findall(r"\w+", exn))
        if not a or not b:
            continue
        overlap = len(a & b) / max(1, len(a | b))
        if overlap > 0.9:
            return 0.1
    return 1.0


def _security_sensitivity_score(patch_text: str, finding: AgentFinding) -> float:
    """
    If a finding is security related, patches that mention vault/env/rotate get a boost.
    If patch tries to simply print secrets or echo them, penalize.
    """
    txt = (patch_text or "").lower()
    if not txt:
        return 0.0
    if (finding.category or "").lower() == "security":
        positives = ["vault", "env(", "os.getenv", "rotate", "kms", "secret_manager", "secretmanager", "hash", "mask"]
        negatives = ["print(", "echo ", "base64_decode", "decode("]
        pos_score = 0.0
        for p in positives:
            if p in txt:
                pos_score = 1.0
                break
        neg_score = 0.0
        for n in negatives:
            if n in txt:
                neg_score = 1.0
                break
        return max(0.0, pos_score - 0.8 * neg_score)
    else:
        # non-security: penalize if patch tries to disable security controls
        if "disable" in txt and ("sast" in txt or "scan" in txt or "check" in txt):
            return 0.0
        return 0.5


def _syntactic_quality_score(patch_text: str) -> float:
    """
    Very cheap heuristic: count code-like tokens, reasonable density, presence of comment lines,
    not only binary/hex dumps.
    """
    if not patch_text:
        return 0.0
    # code-like tokens
    tokens = re.findall(r"[A-Za-z_]\w*", patch_text)
    if not tokens:
        return 0.0
    token_density = len(tokens) / max(1, len(patch_text.splitlines()))
    # clamp
    td = min(5.0, token_density) / 5.0
    # prefer patches that contain at least one comment or newline
    has_comment = bool(re.search(r"#|//|/\*", patch_text))
    has_newline = "\n" in patch_text
    base = 0.4 * td + (0.2 if has_comment else 0.0) + (0.2 if has_newline else 0.0)
    return min(1.0, base)


def score_patch_for_finding(
    review: SynthesizedReview,
    finding: AgentFinding,
    patch_id: str,
    patch_text: str,
) -> PatchScore:
    """
    Scores a single patch (patch_id -> patch_text) for a specific finding.
    Returns PatchScore with breakdown.
    """
    try:
        breakdown = {}
        breakdown["rule_match"] = _rule_match_score(patch_text, finding)
        breakdown["message_overlap"] = _message_substring_score(patch_text, finding)
        breakdown["length"] = _length_score(patch_text)
        breakdown["novelty"] = _novelty_score(patch_text, review)
        breakdown["security_sensitive"] = _security_sensitivity_score(patch_text, finding)
        breakdown["syntactic_quality"] = _syntactic_quality_score(patch_text)

        # weighted linear combination — tuneable
        weights = {
            "rule_match": 2.0,
            "message_overlap": 1.5,
            "length": 0.8,
            "novelty": 1.2,
            "security_sensitive": 1.2,
            "syntactic_quality": 1.0,
        }

        # base normalization
        total = 0.0
        weight_sum = 0.0
        for k, w in weights.items():
            v = float(breakdown.get(k, 0.0) or 0.0)
            total += w * v
            weight_sum += w

        # small penalty for patches that are identical to finding.message (low-value)
        if finding.message and patch_text.strip() == finding.message.strip():
            total *= 0.6

        # final score between 0 and 1
        raw = (total / weight_sum) if weight_sum > 0 else 0.0
        # small smoothing / sigmoid-like transform to push extremes outward
        final_score = float(1.0 / (1.0 + math.exp(-6 * (raw - 0.5))))
        ps = PatchScore(
            finding_id=str(finding.id),
            patch_id=str(patch_id),
            score=final_score,
            breakdown=breakdown,
            reason=None,
        )
        logger.info("patch_scored", finding_id=finding.id, patch_id=patch_id, score=final_score)
        return ps
    except Exception as e:
        logger.error("patch_scoring_error", error=str(e))
        return PatchScore(finding_id=str(finding.id), patch_id=str(patch_id), score=0.0, breakdown={}, reason=str(e))


# Convenience: score all patches across a review and return list sorted desc
def score_all_patches(review: SynthesizedReview) -> list[PatchScore]:
    out = []
    sp = getattr(review, "suggested_patches", {}) or {}
    if not sp:
        return out
    # map finding ids
    fid_map = {str(f.id): f for f in (review.findings or [])}
    # For each patch, try to assign to the most relevant finding(s).
    for pid, ptext in sp.items():
        # attempt direct mapping by finding id
        if pid in fid_map:
            out.append(score_patch_for_finding(review, fid_map[pid], pid, ptext))
            continue
        # attempt fingerprint mapping: patch keys sometimes fingerprint -> map via metadata
        fp_index = (review.metadata or {}).get("fingerprint_index", {}) or {}
        if pid in fp_index:
            for fid in fp_index[pid]:
                f = fid_map.get(fid)
                if f:
                    out.append(score_patch_for_finding(review, f, pid, ptext))
            continue
        # fallback: score against every finding and keep top match
        best = None
        for f in (review.findings or []):
            sc = score_patch_for_finding(review, f, pid, ptext)
            if best is None or sc.score > best.score:
                best = sc
        if best:
            out.append(best)
    # sort
    out.sort(key=lambda x: x.score, reverse=True)
    return out