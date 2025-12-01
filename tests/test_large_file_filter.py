# tests/test_large_file_filter.py
import os
import pytest
from sanitizers.large_file_filter import filter_files

LONG = "A" * 25000
SHORT = "print(1)\n"

def test_partial_truncate_default():
    files = {"big.py": LONG}
    filtered, meta = filter_files(files)  # uses defaults: max=20000, truncate_to=5000
    assert "big.py" in filtered
    assert len(filtered["big.py"]) == int(os.environ.get("MACRS_LARGE_FILE_TRUNCATE_TO", "5000"))
    assert meta["big.py"]["truncated"] is True
    assert meta["big.py"]["skipped"] is False

def test_skip_if_truncate_disabled():
    files = {"big.py": LONG}
    filtered, meta = filter_files(files, max_chars=10000, truncate_to=0)  # disable truncation -> skip
    assert "big.py" not in filtered
    assert meta["big.py"]["skipped"] is True
    assert meta["big.py"]["reason"] == "too_big"

def test_keep_small_file():
    files = {"s.py": SHORT}
    filtered, meta = filter_files(files)
    assert "s.py" in filtered
    assert filtered["s.py"] == SHORT
    assert meta["s.py"]["skipped"] is False
    assert meta["s.py"]["truncated"] is False