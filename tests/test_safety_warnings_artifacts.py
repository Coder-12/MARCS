# tests/test_safety_warnings_artifacts.py
import json
import asyncio
from orchestration.orchestrator import DemoOrchestrator

async def _run_demo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text("print('x')\n")

    # patch with suspicious code
    review = {
        "findings": [],
        "suggested_patches": {
            "x.py": (
                "--- a/x.py\n"
                "+++ b/x.py\n"
                "@@ -1,1 +1,2 @@\n"
                " print('x')\n"
                "+eval('2+2')\n"
            )
        }
    }

    orch = DemoOrchestrator(artifact_dir=str(tmp_path / "artifacts"))
    patches = orch._summarize_patches(review)

    # safety map constructed manually
    safety_map = {}
    for fname, ptxt in patches:
        warnings = orch.run_safety_checks(fname, ptxt)
        if warnings:
            safety_map[fname] = warnings

    # store safety map for artifacts
    orch._latest_safety_map = safety_map

    apply_res = orch.apply_selected("evt1", str(repo), {"x.py": patches[0][1]}, dry_run=True)
    apply_res["safety_warnings"] = safety_map

    art = orch.write_artifacts("evt1", review, apply_res)
    return art, safety_map


def test_safety_warnings_in_artifacts(tmp_path):
    art, safety_map = asyncio.run(_run_demo(tmp_path))

    # --- Check review.json ---
    with open(art["review"], "r", encoding="utf-8") as fh:
        review_art = json.load(fh)
    assert "_safety_warnings" in review_art
    assert review_art["_safety_warnings"] == safety_map

    # --- Check apply_result.json ---
    with open(art["apply_result"], "r", encoding="utf-8") as fh:
        apply_art = json.load(fh)
    assert "safety_warnings" in apply_art
    assert apply_art["safety_warnings"] == safety_map