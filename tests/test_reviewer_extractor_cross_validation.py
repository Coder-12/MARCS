# tests/test_reviewer_extractor_cross_validation.py
import pytest
import json
from sanitizers.reviewer_extractor_validator import cross_validate

def test_drop_patch_for_missing_file():
    healed = {
        "findings": [],
        "suggested_patches": {"nope.txt": "+++ nope\n+ added"},
        "summary": ""
    }
    prompt_files = {"a.py": "print('hi')"}
    out, meta = cross_validate(healed, prompt_files, cap_meta={})
    assert out["suggested_patches"] == {}
    assert "nope.txt" in meta["dropped_patches"]
    assert meta["dropped_patches"]["nope.txt"]["reason"] == "missing_target"

def test_keep_patch_with_file_marker():
    healed = {
        "findings": [],
        "suggested_patches": {"a.py": "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-print('x')\n+print('y')"},
        "summary": ""
    }
    prompt_files = {"a.py": "print('x')"}
    out, meta = cross_validate(healed, prompt_files, cap_meta={})
    assert "a.py" in out["suggested_patches"]
    assert meta["kept_patches"] == ["a.py"]

def test_wrap_findings_and_patch_reference_by_fid():
    healed = {
        "findings": [{"id": "f1", "message": "ok"}],
        "suggested_patches": {"f1": "+++ a/foo.py\n+new"},
        "summary": ""
    }
    prompt_files = {"foo.py": "old"}
    out, meta = cross_validate(healed, prompt_files, cap_meta={})
    # patch keyed by finding id with no file marker -> is accepted but flagged no_file_marker
    assert "f1" in meta["kept_patches"] or ("f1" in meta["dropped_patches"] and meta["dropped_patches"]["f1"]["reason"]!="missing_target")