# tests/test_diff_generator.py
import os
from services.diff_generator import generate_unified_diff, write_final_artifact, generate_repo_diff_from_applied
import tempfile
import json

SIMPLE_ORIG = """def add(a, b):
    return a + b
"""

SIMPLE_NEW = """def add(a, b):
    # guarded
    return a + b
"""

def test_generate_unified_diff_new_lines_and_hunk():
    diff = generate_unified_diff(SIMPLE_ORIG, SIMPLE_NEW, path="simple.py", context=3)
    # header present
    assert "--- a/simple.py" in diff or "--- simple.py" in diff or "--- a/simple.py" in diff
    assert "+++ b/simple.py" in diff or "+++ b/simple.py" in diff
    # hunk marker and added line present
    assert "@@ " in diff
    assert "+    # guarded" in diff

def test_write_final_artifact_and_repo_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    f = repo / "simple.py"
    f.write_text(SIMPLE_ORIG)
    applied_map = {"simple.py": SIMPLE_NEW}
    diffs = generate_repo_diff_from_applied(str(repo), applied_map)
    assert "simple.py" in diffs
    assert "+    # guarded" in diffs["simple.py"]
    art_path = write_final_artifact("evt-123", "me/repo", findings=[], applied_diffs=diffs, summary="ok", artifacts_dir=str(tmp_path))
    assert os.path.exists(art_path)
    content = json.loads(open(art_path, "r", encoding="utf-8").read())
    assert content["event_id"] == "evt-123"
    assert "simple.py" in content["applied_patches"]