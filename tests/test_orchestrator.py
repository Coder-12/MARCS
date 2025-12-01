# tests/test_orchestrator.py
import os
import json
import shutil
import tempfile

import pytest

from orchestration.orchestrator import DemoOrchestrator
from journals import journal as journal_mod
from services.patch_applier import apply_patches

# Small helpers / fixtures content
SIMPLE_ORIG = "def add(a, b):\n    return a + b\n"
SIMPLE_PATCH = """--- a/simple.py
+++ b/simple.py
@@ -1,2 +1,3 @@
 def add(a, b):
-    return a + b
+    # guarded
+    return a + b
"""

CREATE_NEW_PATCH = """--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+def hello():
+    return "hi"
"""

HELLO_ORIG = "print('hello')\n"
HELLO_PATCH = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,2 @@
 print('hello')
+print('world')
"""

@pytest.mark.asyncio
async def test_orchestrator_preview(tmp_path, monkeypatch):
    """
    Preview-only: pass a precomputed review (no reviewer call),
    ensure preview text contains patch and artifacts written by write_artifacts.
    """
    art_dir = tmp_path / "artifacts"
    monkeypatch.setenv("MACRS_ARTIFACT_DIR", str(art_dir))
    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    # create a sample repo (not used for preview but consistent)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text(HELLO_ORIG)

    # supply a review dict (precomputed)
    review = {
        "findings": [],
        "suggested_patches": {
            "p1": HELLO_PATCH
        }
    }

    # Use run_review via event with embedded review to exercise run_review path
    event = {"event_id": "evt-preview", "repo_root": str(repo), "review": review}
    got_review = await orch.run_review(event)  # should immediately return the supplied review
    assert got_review is review

    patches = orch._summarize_patches(got_review)
    assert len(patches) == 1
    preview = orch.render_patch_preview(patches)
    assert "PATCH p1" in preview or "PATCH hello.py" in preview
    assert "print('world')" in preview

    # write artifacts and ensure review.json exists
    artifacts = orch.write_artifacts("evt-preview", got_review, apply_result=None)
    assert "review" in artifacts
    assert os.path.exists(artifacts["review"])
    data = json.loads(open(artifacts["review"], "r", encoding="utf-8").read())
    assert data["suggested_patches"]["p1"] == HELLO_PATCH

def _read_journal_actions(event_id):
    j = journal_mod.read_journal(event_id)
    return [e.get("action") for e in j]

def test_orchestrator_apply_dry_run(tmp_path, monkeypatch):
    """
    Dry-run apply: file must remain unchanged, journal must record dry_run entry,
    backups must NOT be created.
    """
    # Setup envs to isolate data
    art_dir = tmp_path / "artifacts"
    backup_dir = tmp_path / "backups"
    journal_dir = tmp_path / "journals"

    monkeypatch.setenv("MACRS_ARTIFACT_DIR", str(art_dir))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(journal_dir))

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    # repo + file
    repo = tmp_path / "repo"
    repo.mkdir()
    fpath = repo / "simple.py"
    fpath.write_text(SIMPLE_ORIG)

    # build selected patches mapping (simulate selected_map)
    selected = {"simple.py": SIMPLE_PATCH}

    event_id = "evt-dryrun"
    # ensure no leftover journal
    journal_mod.clear_journal(event_id)

    # dry-run apply through orchestrator helper
    res = orch.apply_selected(event_id, str(repo), selected, dry_run=True)
    assert res.get("dry_run", False) is True

    # file must be unchanged
    assert fpath.read_text() == SIMPLE_ORIG

    # backup dir should not contain event backup (no backup in dry-run)
    assert not (backup_dir / event_id).exists()

    # journal should exist and contain dry_run_complete action
    actions = _read_journal_actions(event_id)
    assert any(a == "dry_run_complete" for a in actions), f"journal actions: {actions}"

def test_orchestrator_apply_real(tmp_path, monkeypatch):
    """
    Real apply: file should change, backup created, applied entry present.
    """
    art_dir = tmp_path / "artifacts"
    backup_dir = tmp_path / "backups"
    journal_dir = tmp_path / "journals"

    monkeypatch.setenv("MACRS_ARTIFACT_DIR", str(art_dir))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(backup_dir))
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(journal_dir))

    orch = DemoOrchestrator(artifact_dir=str(art_dir))

    repo = tmp_path / "repo"
    repo.mkdir()
    fpath = repo / "hello.py"
    fpath.write_text(HELLO_ORIG)

    selected = {"hello.py": HELLO_PATCH}
    event_id = "evt-realapply"
    journal_mod.clear_journal(event_id)

    res = orch.apply_selected(event_id, str(repo), selected, dry_run=False)
    # successful application should list applied file
    applied = res.get("applied", {})
    assert "hello.py" in applied, f"apply result: {res}"

    # file content changed
    txt = fpath.read_text()
    assert "print('world')" in txt

    # backup exists
    bdir = backup_dir / event_id
    assert bdir.exists()
    # journal should contain commit
    actions = _read_journal_actions(event_id)
    assert any(a == "commit" for a in actions), f"journal actions: {actions}"