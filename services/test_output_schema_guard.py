# tests/test_output_schema_guard.py
import json
import pytest
from sanitizers.output_schema_guard import validate_and_heal_review_json

def test_heal_basic_missing_keys():
    parsed = {}
    healed, meta = validate_and_heal_review_json(parsed, repo="me/repo")
    assert healed["findings"] == []
    assert healed["suggested_patches"] == {}
    assert healed["summary"] == ""
    assert "added_missing_findings" in meta["repaired"]

def test_wrap_list_into_findings():
    parsed = [
        {"id": "f1", "message": "m"},
        {"message": "m2"}
    ]
    healed, meta = validate_and_heal_review_json(parsed, repo="me/repo")
    assert isinstance(healed["findings"], list)
    assert healed["findings"][0]["id"] == "f1"
    assert healed["findings"][1]["id"].startswith("me_repo-f")

def test_normalize_severity_and_confidence():
    parsed = {"findings": [{"severity": "High", "confidence": "0.8", "message": "x"}]}
    healed, meta = validate_and_heal_review_json(parsed, repo="me/repo")
    f = healed["findings"][0]
    assert f["severity"] == "high"
    assert isinstance(f["confidence"], float)
    assert 0.79 < f["confidence"] <= 0.8

def test_patch_safety_drop_dangerous():
    parsed = {"suggested_patches": {"p1": "rm -rf /"}}
    healed, meta = validate_and_heal_review_json(parsed, repo="me/repo")
    assert healed["suggested_patches"] == {}
    assert "p1" in meta["patch_safety"]
    assert meta["patch_safety"]["p1"]["ok"] is False

def test_large_patch_truncated():
    big = "a" * (int(__import__("os").environ.get("MACRS_MAX_PATCH_CHARS", "5000")) + 10)
    parsed = {"suggested_patches": {"p1": big}}
    healed, meta = validate_and_heal_review_json(parsed, repo="me/repo")
    assert healed["suggested_patches"].get("p1") is not None or "p1" not in healed["suggested_patches"]
    assert "p1" in meta["patch_safety"]