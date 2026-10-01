# tests/test_journal_inspector.py
import os
import json
import pytest
from journals import journal
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


def test_malformed_journal_prevents_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path / "journals"))
    target = tmp_path / "target.py"
    target.write_text("keep me\n")
    journal.append_entry("broken", "file_replaced", {"file": "target.py", "path": str(target)})
    path = journal.journal_path("broken")
    with open(path, "a") as fh:
        fh.write('{"action":')
    before = open(path).read()
    with pytest.raises(ValueError, match="line 2"):
        journal_inspector.inspect_and_recover_one(path)
    result = journal_inspector.scan_and_recover_all()
    assert result[0]["action"] == "error"
    assert "line 2" in result[0]["error"]
    assert target.read_text() == "keep me\n"
    assert open(path).read() == before


def test_inspector_rejects_journal_outside_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path / "journals"))
    path = tmp_path / "outside.jsonl"
    path.write_text('{"action": "start"}\n')
    with pytest.raises(ValueError, match="configured journal directory"):
        journal_inspector.inspect_and_recover_one(str(path))


def test_recovery_uses_shared_writer_and_reports_write_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path / "journals"))
    journal.append_entry("event", "start")
    journal_inspector.perform_resume_commit("event", [{"action": "file_replaced", "file": "a.py"}])
    assert journal.read_journal("event")[-1] == {"action": "commit", "applied_files": ["a.py"], "ts": "inspector"}

    def fail(*args, **kwargs):
        raise OSError("test write failure")

    monkeypatch.setattr(journal, "append_entry", fail)
    assert journal_inspector.perform_resume_commit("event", []) == {"ok": False}
    with pytest.raises(OSError, match="test write failure"):
        journal_inspector.perform_rollback("event", [])


def test_legacy_envelope_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path))
    target = tmp_path / "a.py"
    backup = tmp_path / "a.py.bak"
    target.write_text("patched\n")
    backup.write_text("original\n")
    records = [
        {"op": "backup_created", "payload": {"file": "a.py", "backup": str(backup)}},
        {"action": "file_replaced", "op": "file_replaced", "payload": {"file": "a.py", "path": str(target)}},
        {"op": "error", "payload": {"error": "simulated failure"}},
    ]
    path = tmp_path / "legacy.jsonl"
    _write_journal_lines(path, records)
    result = journal_inspector.inspect_and_recover_one(str(path))
    assert result["action"] == "rollback"
    assert target.read_text() == "original\n"
    assert journal.read_journal("legacy")[-1] == {"action": "rollback", "ts": "inspector"}
    stored = [json.loads(line) for line in path.read_text().splitlines()]
    assert stored[:-1] == records
    assert "op" not in stored[-1]


@pytest.mark.parametrize("operation", ["restore", "remove"])
def test_failed_rollback_reports_error_and_remains_retryable(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path / "journals"))
    target = tmp_path / "a.py"
    target.write_text("patched\n")
    if operation == "restore":
        backup = tmp_path / "a.py.bak"
        backup.write_text("original\n")
        journal.append_entry("event", "backup_created", {"file": "a.py", "backup": str(backup)})
        helper = "_copy_backup_to_target"
    else:
        helper = "_remove_target_path"
    journal.append_entry("event", "file_replaced", {"file": "a.py", "path": str(target)})
    journal.append_entry("event", "error")
    path = tmp_path / "journals" / "event.jsonl"
    before = path.read_bytes()
    with monkeypatch.context() as failure:
        failure.setattr(journal_inspector, helper, lambda *args: False)
        result = journal_inspector.scan_and_recover_all()
    assert result[0]["action"] == "error"
    assert "rollback incomplete" in result[0]["error"]
    assert path.read_bytes() == before
    assert target.read_text() == "patched\n"
    retry = journal_inspector.scan_and_recover_all()
    assert retry[0]["action"] == "rollback"
    assert journal.read_journal("event")[-1]["action"] == "rollback"
    if operation == "restore":
        assert target.read_text() == "original\n"
    else:
        assert not target.exists()


def test_failed_commit_marker_reports_error_and_remains_retryable(tmp_path, monkeypatch):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path / "journals"))
    journal.append_entry("event", "backup_created", {"file": "a.py"})
    journal.append_entry("event", "file_replaced", {"file": "a.py"})
    path = tmp_path / "journals" / "event.jsonl"
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("test write failure")

    with monkeypatch.context() as failure:
        failure.setattr(journal, "append_entry", fail)
        result = journal_inspector.scan_and_recover_all()
    assert result[0]["action"] == "error"
    assert "commit marker write failed" in result[0]["error"]
    assert path.read_bytes() == before
    retry = journal_inspector.scan_and_recover_all()
    assert retry[0]["action"] == "resume_commit"
    assert journal.read_journal("event")[-1]["action"] == "commit"
