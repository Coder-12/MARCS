# tests/test_patch_applier.py
import os
import tempfile
import shutil
import json
from services.patch_applier import parse_unified_diff, apply_patches
from journals import journal

SIMPLE_ORIG = """def add(a, b):
    return a + b
"""

SIMPLE_PATCH = """--- a/simple.py
+++ b/simple.py
@@ -1,2 +1,3 @@
 def add(a, b):
-    return a + b
+    # guarded
+    return a + b
"""

def test_parse_unified_diff_basic():
    parsed = parse_unified_diff(SIMPLE_PATCH)
    assert "files" in parsed
    files = list(parsed["files"].keys())
    assert any("simple.py" in k for k in files)
    # one hunk
    first = parsed["files"][files[0]]
    assert len(first["hunks"]) == 1
    h = first["hunks"][0]
    assert isinstance(h["lines"], list)
    assert any(l.startswith("+") or l.startswith("-") or l.startswith(" ") for l in h["lines"])

def test_apply_patches_dry_run_and_apply(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    f = repo / "simple.py"
    f.write_text(SIMPLE_ORIG)
    event_id = "evt-unit-1"
    journal.clear_journal(event_id)

    # dry run
    res = apply_patches(event_id, str(repo), {"simple.py": SIMPLE_PATCH}, dry_run=True)
    assert res.get("dry_run") is True
    assert "simple.py" not in res.get("dropped", {})
    # apply
    res2 = apply_patches(event_id, str(repo), {"simple.py": SIMPLE_PATCH}, dry_run=False)
    assert "simple.py" in res2["applied"]
    # file content changed
    new_text = (repo / "simple.py").read_text()
    assert "# guarded" in new_text
    # backup exists
    bdir = os.path.join("backups", event_id)
    assert os.path.exists(bdir)
    # journal exists and has entries
    j = journal.read_journal(event_id)
    assert any(e["action"] == "commit" for e in j)
