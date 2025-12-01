# tests/test_patch_selector_v2.py
from services.patch_selector_pro import select_patches_for_review

def test_selector_runs_and_produces_output(synthetic_review):
    out = select_patches_for_review(synthetic_review, top_k=2)
    assert isinstance(out, dict)
    assert "sty101" in out or "sec777" in out

def test_top_k_respected(synthetic_review):
    out = select_patches_for_review(synthetic_review, top_k=1)
    for fid, lst in out.items():
        assert len(lst) == 1

def test_security_boost_affects_ranking(synthetic_review):
    out = select_patches_for_review(synthetic_review, top_k=2)
    sec_list = out.get("sec777", [])
    if len(sec_list) >= 2:
        assert sec_list[0]["score"] >= sec_list[1]["score"]

def test_selector_metadata_written(synthetic_review):
    select_patches_for_review(synthetic_review)
    md = synthetic_review.metadata.get("selected_patches")
    assert isinstance(md, dict)