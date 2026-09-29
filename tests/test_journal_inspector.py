# tests/test_journal_inspector.py
import os
import json
from services import journal_inspector


def _write_journal_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        for l in lines:
            fh.write(json.dumps(l) + "\n")


def test_inspector_no_journals(tmp_path, monkeypatch):
    jd = tmp_path / "journals"
    jd.mkdir()

    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(jd))

    out = journal_inspector.scan_and_recover_all()
    assert out == []


def test_inspector_rollback_when_error(tmp_path, monkeypatch):
    jd = tmp_path / "journals"
    bd = tmp_path / "backups"
    repo = tmp_path / "repo"

    jd.mkdir()
    bd.mkdir()
    repo.mkdir()

    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(jd))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(bd))

    event_id = "evt-err"
    jpath = jd / f"{event_id}.jsonl"

    entries = [
        {"action": "start"},
        {"action": "backup_created", "file": "a.py",
         "backup": str(bd / "a.py.bak")},
        {"action": "file_replaced", "file": "a.py",
         "path": str(repo / "a.py")},
        {"action": "error", "error": "boom"},
    ]
    _write_journal_lines(str(jpath), entries)

    # backup file + patched target file
    (bd / "a.py.bak").write_text("orig\n")
    (repo / "a.py").write_text("patched\n")

    res = journal_inspector.inspect_and_recover_one(str(jpath))
    assert res["action"] == "rollback"
    assert (repo / "a.py").read_text() == "orig\n"


def test_inspector_resume_commit_when_all_backups_present(tmp_path, monkeypatch):
    jd = tmp_path / "journals"
    bd = tmp_path / "backups"
    repo = tmp_path / "repo"

    jd.mkdir()
    bd.mkdir()
    repo.mkdir()

    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(jd))
    monkeypatch.setenv("MACRS_BACKUP_DIR", str(bd))

    event_id = "evt-commit"
    jpath = jd / f"{event_id}.jsonl"

    entries = [
        {"action": "start"},
        {"action": "backup_created", "file": "x.py",
         "backup": str(bd / "x.py.bak")},
        {"action": "file_replaced", "file": "x.py",
         "path": str(repo / "x.py")},
    ]
    _write_journal_lines(str(jpath), entries)

    (bd / "x.py.bak").write_text("content\n")
    (repo / "x.py").write_text("patched\n")

    res = journal_inspector.inspect_and_recover_one(str(jpath))
    assert res["action"] == "resume_commit"

    lines = [json.loads(l) for l in open(str(jpath)).read().splitlines()]
    assert any(l.get("action") == "commit" for l in lines)