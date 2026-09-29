# tests/test_golden_registry.py
import pytest
import os
from services.golden_registry import GoldenRegistry
import json


def test_golden_registry_initial_load():
    reg = GoldenRegistry()
    all_cases = reg.all()

    assert isinstance(all_cases, dict)
    assert reg.loaded_at() is not None


def test_golden_registry_reload(monkeypatch, tmp_path):
    gc_root = tmp_path / "golden"
    gc_root.mkdir()

    v1 = gc_root / "v1"
    v1.mkdir()

    file = v1 / "test_case.json"
    file.write_text(
        json.dumps({
            "id": "test123",
            "description": "test",
            "headers": {"X-GitHub-Delivery": "test123"},
            "payload": {},
            "expected": {
                "min_findings": 0,
                "max_findings": 10,
                "categories": [],
                "min_patches": 0,
                "allow_extra_findings": True
            }
        }),
        encoding="utf-8"
    )

    from eval import loader as golden_loader

    # IMPORTANT FIX → patch to gc_root (the actual golden directory)
    monkeypatch.setattr(
        golden_loader,
        "BASE_DIR",
        str(gc_root)
    )

    reg = GoldenRegistry()
    reg.reload()

    all_cases = reg.all()
    assert "test123" in all_cases
    assert reg.loaded_at() is not None


def test_golden_registry_get():
    reg = GoldenRegistry()
    all_cases = reg.all()

    if not all_cases:
        pytest.skip("No golden cases available to test .get")

    some_id = next(iter(all_cases.keys()))
    case = reg.get(some_id)
    assert case is not None
    assert case["id"] == some_id