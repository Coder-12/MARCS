import json
from pathlib import Path

import pytest

from journals import journal


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    directory = tmp_path / "journal-data"
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(directory))
    return directory


def test_append_read_round_trip(journal_dir):
    assert journal.read_journal("event") == []
    journal.append_entry("event", "start", {"repo_root": "répertoire"})
    journal.append_entry("event", "commit", {"applied_files": ["a.py"]})
    assert journal.read_journal("event") == [
        {"action": "start", "repo_root": "répertoire"},
        {"action": "commit", "applied_files": ["a.py"]},
    ]
    raw = (journal_dir / "event.jsonl").read_text(encoding="utf-8")
    assert "répertoire" in raw
    assert raw.endswith("\n")
    assert all("op" not in json.loads(line) for line in raw.splitlines())


def test_serialization_is_repeatable(journal_dir):
    journal.append_entry("first", "start", {"b": 2, "a": 1})
    journal.append_entry("second", "start", {"a": 1, "b": 2})
    assert (journal_dir / "first.jsonl").read_bytes() == (journal_dir / "second.jsonl").read_bytes()


def test_clear_is_idempotent_and_scoped(journal_dir):
    journal.append_entry("event", "start")
    journal.append_entry("other", "start")
    journal.clear_journal("event")
    journal.clear_journal("event")
    assert journal.read_journal("event") == []
    assert journal.read_journal("other") == [{"action": "start"}]


def test_directory_is_resolved_at_call_time(journal_dir, tmp_path, monkeypatch):
    journal.append_entry("event", "start")
    other = tmp_path / "other"
    monkeypatch.setenv("MACRS_JOURNAL_DIR", str(other))
    assert Path(journal.journal_path("event")) == other / "event.jsonl"
    assert journal.read_journal("event") == []
    journal.append_entry("event", "commit")
    assert journal.read_journal("event") == [{"action": "commit"}]
    assert json.loads((journal_dir / "event.jsonl").read_text()) == {"action": "start"}


@pytest.mark.parametrize("configuration", ["relative", "symlink"])
def test_paths_stay_inside_resolved_directory(tmp_path, monkeypatch, configuration):
    directory = tmp_path / "journals"
    directory.mkdir()
    monkeypatch.chdir(tmp_path)
    if configuration == "relative":
        configured = "journals"
    else:
        alias = tmp_path / "journal-alias"
        alias.symlink_to(directory, target_is_directory=True)
        configured = str(alias)
    monkeypatch.setenv("MACRS_JOURNAL_DIR", configured)
    path = Path(journal.journal_path("event-1"))
    assert path.resolve().parent == Path(configured).resolve() == directory.resolve()
    journal.append_entry("event-1", "start")
    assert journal.read_journal("event-1") == [{"action": "start"}]
    assert (directory / "event-1.jsonl").is_file()


@pytest.mark.parametrize("event_id", ["", ".", "..", "../escape", "/tmp/escape", "a/b", "a\\b", "C:\\escape", "x\n", "x\x00", None])
def test_unsafe_event_rejected(journal_dir, event_id):
    for operation in (journal.journal_path, journal.read_journal, journal.clear_journal):
        with pytest.raises(ValueError):
            operation(event_id)
    with pytest.raises(ValueError):
        journal.append_entry(event_id, "start")
    assert not journal_dir.exists()


def test_symlink_file_rejected(journal_dir, tmp_path):
    journal_dir.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"action": "start"}\n')
    (journal_dir / "event.jsonl").symlink_to(outside)
    for operation in (journal.journal_path, journal.read_journal, journal.clear_journal):
        with pytest.raises(ValueError, match="symlink"):
            operation("event")
    with pytest.raises(ValueError, match="symlink"):
        journal.append_entry("event", "commit")
    assert outside.read_text() == '{"action": "start"}\n'


@pytest.mark.parametrize("record", ['{broken', '[]', '{}', '{"action": ""}', '{"op": "commit"}', '{"action": "start", "op": "start"}', '{"action": "start", "value": NaN}'])
def test_malformed_record_stops_reading(journal_dir, record):
    journal.append_entry("event", "start")
    with (journal_dir / "event.jsonl").open("a") as fh:
        fh.write(record + "\n")
    with pytest.raises(ValueError, match="line 2"):
        journal.read_journal("event")


@pytest.mark.parametrize("action,data", [("", {}), (None, {}), ("start", {"action": "commit"}), ("start", {"op": "commit"}), ("start", {"x": float("nan")})])
def test_invalid_append_writes_nothing(journal_dir, action, data):
    with pytest.raises(ValueError):
        journal.append_entry("event", action, data)
    assert not journal_dir.exists()


def test_filesystem_errors_propagate(journal_dir):
    (journal_dir / "event.jsonl").mkdir(parents=True)
    with pytest.raises(IsADirectoryError):
        journal.read_journal("event")
    with pytest.raises(IsADirectoryError):
        journal.append_entry("event", "start")
    with pytest.raises(OSError):
        journal.clear_journal("event")
    assert (journal_dir / "event.jsonl").is_dir()


@pytest.mark.parametrize("with_action", [False, True])
def test_legacy_records_normalized_only_on_read(journal_dir, with_action):
    journal_dir.mkdir()
    record = {"op": "file_replaced", "payload": {"file": "a.py", "path": "/repo/a.py"}, "ts": "historical"}
    if with_action:
        record["action"] = record["op"]
    path = journal_dir / "legacy.jsonl"
    original = json.dumps(record) + "\n"
    path.write_text(original)
    assert journal.read_journal("legacy") == [{"action": "file_replaced", "file": "a.py", "path": "/repo/a.py", "ts": "historical"}]
    assert path.read_text() == original
    journal.append_entry("legacy", "commit", {"applied_files": ["a.py"]})
    assert json.loads(path.read_text().splitlines()[-1]) == {"action": "commit", "applied_files": ["a.py"]}


@pytest.mark.parametrize("record", [
    {"action": "commit", "op": "error", "payload": {}},
    {"op": "file_replaced", "file": "a.py", "payload": {"file": "b.py"}},
])
def test_conflicting_legacy_fields_rejected(journal_dir, record):
    journal_dir.mkdir()
    (journal_dir / "legacy.jsonl").write_text(json.dumps(record) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        journal.read_journal("legacy")


@pytest.mark.parametrize("record", [
    '{"action":"error","action":"commit"}',
    '{"op":"error","op":"commit","payload":{}}',
    '{"op":"file_replaced","payload":{"file":"a.py","file":"b.py"}}',
])
def test_duplicate_fields_rejected_without_rewriting(journal_dir, record):
    journal.append_entry("event", "start")
    path = journal_dir / "event.jsonl"
    with path.open("a") as fh:
        fh.write(record + "\n")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="line 2") as error:
        journal.read_journal("event")
    assert "duplicate journal field" in str(error.value.__cause__)
    assert path.read_bytes() == before
