# tests/test_reviewer_crash_resume.py
import os
import json
import tempfile
import asyncio
import shutil

from services.reviewer import Reviewer
from services.journal_inspector import inspect_and_recover_one, scan_and_recover_all
from services.patch_applier import apply_patches
from journals import journal
from services.journal_inspector import get_journal_dir, get_backup_dir


# Helper: write journal entries with no trailing errors
def _write_journal_lines(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")

# A minimal MockLLM that proposes a known patch
class MockLLM_OnePatch:
    model = "mock-test"

    async def chat_complete(self, messages, temperature, max_tokens):
        # Always return a single valid patch for hello.py
        content = json.dumps({
            "findings": [
                {"id": "f1", "category": "style", "severity": "low",
                 "message": "add print", "explanation": "", "confidence": 0.9}
            ],
            "suggested_patches": {
                "f1": "--- a/hello.py\n+++ b/hello.py\n@@ -1,1 +1,2 @@\n print('hello')\n+print('world')\n"
            },
            "summary": "ok"
        })
        return {"choices": [{"message": {"content": content}}], "usage": {}}

# -------------------------------------------------------------------
# TEST 1 — NOOP: crash before file backup or replacement
# -------------------------------------------------------------------
def test_reviewer_integration_noop(tmp_path, monkeypatch):
    journal_dir = tmp_path / "journals"
    backup_dir = tmp_path / "backups"
    journal_dir.mkdir()
    backup_dir.mkdir()

    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(journal_dir))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(backup_dir))

    event_id = "evt-noop"
    jpath = journal_dir / f"{event_id}.jsonl"

    # Only "start" entry → nothing to resume or rollback
    _write_journal_lines(jpath, [
        {"action": "start"}
    ])

    res = inspect_and_recover_one(str(jpath))
    assert res["action"] == "noop"

# -------------------------------------------------------------------
# TEST 2 — ROLLBACK: crash after backup_created but BEFORE file_replaced
# -------------------------------------------------------------------
def test_reviewer_integration_rollback(tmp_path, monkeypatch):
    journal_dir = tmp_path / "journals"
    backup_dir = tmp_path / "backups"
    repo = tmp_path / "repo"

    journal_dir.mkdir()
    backup_dir.mkdir()
    repo.mkdir()

    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(journal_dir))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(backup_dir))

    event_id = "evt-rollback"
    jpath = journal_dir / f"{event_id}.jsonl"

    # Create backup but do NOT create file_replaced
    entries = [
        {"action": "start"},
        {"action": "backup_created", "file": "x.py",
         "backup": str(backup_dir / "x.py.bak")}
    ]
    _write_journal_lines(jpath, entries)

    # Pretend backup exists
    (backup_dir / "x.py.bak").write_text("original-content\n")

    # No replaced file created → rollback MUST restore nothing but mark rollback
    res = inspect_and_recover_one(str(jpath))

    assert res["action"] == "rollback"
    assert "restored" in res["result"]  # even if empty
    assert "removed" in res["result"]

    # Journal must contain rollback entry
    lines = [json.loads(l) for l in open(jpath).read().splitlines()]
    assert any(e.get("action") == "rollback" for e in lines)


# -------------------------------------------------------------------
# TEST 3 — RESUME_COMMIT: crash AFTER file_replaced but BEFORE commit
# -------------------------------------------------------------------
def test_reviewer_integration_resume_commit(tmp_path, monkeypatch):
    journal_dir = tmp_path / "journals"
    backup_dir = tmp_path / "backups"
    repo = tmp_path / "repo"

    journal_dir.mkdir()
    backup_dir.mkdir()
    repo.mkdir()

    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(journal_dir))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(backup_dir))

    # Prepare repo file
    (repo / "hello.py").write_text("print('hello')\n")

    patch_text = """--- a/hello.py
+++ b/hello.py
@@ -1,1 +1,2 @@
 print('hello')
+print('world')
"""

    event_id = "evt-crash"
    result = apply_patches(event_id, str(repo), {"hello.py": patch_text}, dry_run=False)

    # Simulate crash: remove commit entry
    jpath = journal_dir / f"{event_id}.jsonl"
    lines = open(jpath).read().splitlines()

    # Remove the final "commit" entry
    filtered = [ln for ln in lines if '"commit"' not in ln]
    with open(jpath, "w") as fh:
        fh.write("\n".join(filtered) + "\n")

    # Run inspector — must RESUME COMMIT
    res = inspect_and_recover_one(str(jpath))
    assert res["action"] == "resume_commit"

    # Journal MUST now have commit entry
    lines_after = [json.loads(l) for l in open(jpath).read().splitlines()]
    assert any(e.get("action") == "commit" for e in lines_after)