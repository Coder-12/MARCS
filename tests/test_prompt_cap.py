# tests/test_prompt_cap.py
import os
from sanitizers.prompt_cap import apply_prompt_cap, PROMPT_TRUNCATION_KEY

def _make_files(sizes):
    # produce files named f0,f1,... with repeated letters
    return {f"f{i}.txt": ("X" * s) for i, s in enumerate(sizes)}

def test_prompt_cap_no_truncation():
    files = _make_files([10, 20, 30])
    capped, meta = apply_prompt_cap(files, max_chars=1000, append_note=False)
    assert set(capped.keys()) == set(files.keys())
    assert meta["dropped_files"] == []
    assert meta["note_added"] is False

def test_prompt_cap_truncates_and_adds_note():
    files = _make_files([1000, 800, 600, 500])
    capped, meta = apply_prompt_cap(files, max_chars=1500, append_note=True, note_text="NOTE")
    # ensure we didn't exceed cap
    total = sum(len(v) for v in capped.values())
    # note key may be present so include it
    assert total <= 1500
    assert meta["cap"] == 1500
    assert meta["dropped_files"]  # some files dropped
    assert meta["note_added"] is True
    assert PROMPT_TRUNCATION_KEY in capped

def test_prompt_cap_sorts_smallest_first():
    files = {"a": "X"*500, "b": "X"*100, "c": "X"*200}
    capped, meta = apply_prompt_cap(files, max_chars=600, append_note=False)
    # Greedy include smallest: b(100) + c(200) + a(500) won't fit a; included should be b + c
    assert "b" in capped and "c" in capped
    assert "a" not in capped

def test_prompt_cap_includes_truncated_single_when_none_fit():
    # cap smaller than smallest file => include smallest truncated
    files = {"big": "X"*5000, "bigger": "X"*6000}
    capped, meta = apply_prompt_cap(files, max_chars=100, append_note=False)
    assert "big" in capped
    assert meta["truncated_in_place"] is True
    assert meta["truncated_bytes"] == 5000 - len(capped["big"])

def test_prompt_cap_metadata_correct():
    files = _make_files([50, 60, 70, 80])
    capped, meta = apply_prompt_cap(files, max_chars=150, append_note=True, note_text="TR")
    # check meta fields presence
    assert set(meta.keys()) >= {"included_files", "dropped_files", "total_chars", "cap", "note_added"}
    assert meta["cap"] == 150