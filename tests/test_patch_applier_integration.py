# tests/test_patch_applier_integration.py
import os
import tempfile
import shutil
from services.patch_applier import apply_patches
from journals import journal

PATCH_CREATE_FILE = """--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,3 @@
+def hello():
+    return "hi"
+
"""

def test_apply_create_new_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    event_id = "evt-create-1"
    journal.clear_journal(event_id)

    res = apply_patches(event_id, str(repo), {"newfile.py": PATCH_CREATE_FILE}, dry_run=True)
    assert res.get("dry_run") is True
    # apply
    res2 = apply_patches(event_id, str(repo), {"newfile.py": PATCH_CREATE_FILE}, dry_run=False)
    assert "newfile.py" in res2["applied"]
    # file exists now
    assert (repo / "newfile.py").exists()
    content = (repo / "newfile.py").read_text()
    assert 'def hello' in content
    # confirm journal commit
    j = journal.read_journal(event_id)
    assert any(e["op"] == "commit" for e in j)