import logging

import pytest

from journals import journal
from services import startup


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("corruption", ['{"action":', '{"action":"error","action":"commit"}\n'])
def test_startup_isolates_malformed_history(tmp_path, monkeypatch, caplog, dry_run, corruption):
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setattr(startup, "INSPECTOR_ENABLED", True)
    monkeypatch.setattr(startup, "INSPECTOR_DRY", dry_run)
    monkeypatch.setattr(startup, "_last_startup_decisions", [])
    corrupt = tmp_path / "a-corrupt.jsonl"
    target = tmp_path / "target.py"
    target.write_text("preserve target\n")
    # A replacement followed by invalid JSON must never trigger partial recovery.
    journal.append_entry("a-corrupt", "file_replaced", {"file": "target.py", "path": str(target)})
    with corrupt.open("a") as fh:
        fh.write(corruption)
    journal.append_entry("z-valid", "start")
    valid = tmp_path / "z-valid.jsonl"
    before = {path: path.read_bytes() for path in (corrupt, valid, target)}

    with caplog.at_level(logging.ERROR):
        startup.run_startup_inspector()

    decisions = startup.get_last_startup_decisions()
    assert len(decisions) == 2
    error, good = decisions
    if dry_run:
        assert error["journal"] == str(corrupt)
        assert error["decision"] == "error"
        assert good == {"journal": str(valid), "decision": "noop"}
        assert "startup_journal_inspection_failed" in caplog.text
    else:
        assert error["event_id"] == "a-corrupt"
        assert error["action"] == "error"
        assert good == {"event_id": "z-valid", "action": "noop"}
        assert "inspect_failed" in caplog.text
    assert "line 2" in error["error"]
    assert all(path.read_bytes() == content for path, content in before.items())
