"""Single-writer JSONL journals used by patch application and recovery.

Records contain a nonempty ``action`` and flat action-specific fields. No clock
or random values are added. Malformed records stop reading rather than allowing
recovery to act on an incomplete history. Concurrent writers are not supported.
Known legacy op/payload envelopes are normalized only when reading.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def get_journal_dir() -> str:
    return os.environ.get("MACRS_JOURNAL_DIR", "journals")


def journal_path(event_id: str) -> str:
    """Resolve a portable event filename, rejecting traversal and symlink files."""
    if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", event_id):
        raise ValueError("event_id must start with an ASCII letter or digit and contain only letters, digits, _, . or -")
    path = Path(get_journal_dir()) / f"{event_id}.jsonl"
    if path.is_symlink():
        raise ValueError("journal file must not be a symlink")
    return str(path)


def _validate_record(record: Any) -> None:
    if not isinstance(record, dict) or not isinstance(record.get("action"), str) or not record["action"].strip():
        raise ValueError("journal record requires a nonempty action string")
    if "op" in record:
        raise ValueError("journal records use action, not op")


def _normalize_record(record: Any) -> dict[str, Any]:
    if isinstance(record, dict) and "op" in record:
        action = record.get("action", record["op"])
        if action != record["op"]:
            raise ValueError("conflicting legacy action and op")
        payload = record.get("payload")
        if not isinstance(payload, dict) or {"action", "op", "payload"}.intersection(payload):
            raise ValueError("invalid legacy journal payload")
        metadata = {key: value for key, value in record.items() if key not in {"action", "op", "payload"}}
        if any(key in metadata and metadata[key] != value for key, value in payload.items()):
            raise ValueError("conflicting legacy journal fields")
        record = {**metadata, **payload, "action": action}
    _validate_record(record)
    return record


def _open(event_id: str, flags: int, mode: str):
    path = journal_path(event_id)
    # Reject a symlink introduced between validation and open as well.
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    return os.fdopen(fd, mode, encoding="utf-8", newline="\n")


def append_entry(event_id: str, action: str, data: dict[str, Any] | None = None) -> None:
    """Append and fsync one UTF-8 record; propagate validation and I/O errors."""
    path = journal_path(event_id)
    if data is not None and not isinstance(data, dict):
        raise ValueError("journal data must be a dictionary")
    if data and ("action" in data or "op" in data):
        raise ValueError("action and op are reserved journal fields")
    record = {"action": action, **(data or {})}
    _validate_record(record)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _open(event_id, os.O_WRONLY | os.O_CREAT | os.O_APPEND, "a") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_journal(event_id: str) -> list[dict[str, Any]]:
    """Read in append order. Missing files are empty; invalid lines raise."""
    try:
        fh = _open(event_id, os.O_RDONLY, "r")
    except FileNotFoundError:
        return []
    entries = []
    with fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = _normalize_record(json.loads(
                    line, parse_constant=_reject_constant, object_pairs_hook=_unique_fields
                ))
            except ValueError as exc:
                raise ValueError(f"invalid journal record for {event_id} at line {number}") from exc
            entries.append(record)
    return entries


def _reject_constant(value: str):
    raise ValueError("non-finite JSON number")


def _unique_fields(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate journal field: {key}")
        record[key] = value
    return record


def clear_journal(event_id: str) -> None:
    """Remove a journal if present; other filesystem errors propagate."""
    Path(journal_path(event_id)).unlink(missing_ok=True)
