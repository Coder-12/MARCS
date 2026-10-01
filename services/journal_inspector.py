# services/journal_inspector.py
from __future__ import annotations

import os
import shutil
import logging
from typing import List, Dict, Any, Optional
from journals import journal

def get_journal_dir() -> str:
    return journal.get_journal_dir()

def get_backup_dir() -> str:
    return os.environ.get("MACRS_BACKUP_DIR", "backups")
# Reuse env constants from patch_applier (keep same defaults)

logger = logging.getLogger("services.journal_inspector")


def _iter_journal_files() -> List[str]:
    if not os.path.exists(get_journal_dir()):
        return []
    out = []
    for fn in os.listdir(get_journal_dir()):
        if fn.endswith(".jsonl"):
            out.append(os.path.join(get_journal_dir(), fn))
    return sorted(out)


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    event_id = _journal_event_id_from_path(path)
    expected = journal.journal_path(event_id)
    if os.path.abspath(path) != os.path.abspath(expected):
        raise ValueError("journal path must be inside the configured journal directory")
    return journal.read_journal(event_id)


def _journal_event_id_from_path(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _find_entries_by_action(entries: List[Dict[str, Any]], action: str) -> List[Dict[str, Any]]:
    return [e for e in entries if e.get("action") == action]


class InspectorDecision:
    ROLLBACK = "rollback"
    RESUME_COMMIT = "resume_commit"
    NOOP = "noop"


def decide_action_for_journal(entries: List[Dict[str, Any]]) -> str:
    """
    Conservative, explicit logic:

      - If no entries -> NOOP
      - If 'commit' or 'rollback' already recorded -> NOOP (nothing to do)
      - If 'error' recorded -> ROLLBACK (we failed during apply)
      - If neither file_replaced nor backup_created -> NOOP
      - If backup_created present but no file_replaced -> ROLLBACK
          (safety: stray backups without any replacement -> suspicious / rollback)
      - If file_replaced entries exist:
          * If any replaced file lacks a corresponding backup_created -> ROLLBACK
          * Otherwise -> RESUME_COMMIT
    """
    if not entries:
        return InspectorDecision.NOOP

    actions = [e.get("action") for e in entries]

    # Already finished or rolled back
    if "commit" in actions or "rollback" in actions:
        return InspectorDecision.NOOP

    # If an explicit error happened -> rollback
    if "error" in actions:
        return InspectorDecision.ROLLBACK

    file_replaced = _find_entries_by_action(entries, "file_replaced")
    backups_created = _find_entries_by_action(entries, "backup_created")

    # Neither replacement nor backups -> nothing to do
    if not file_replaced and not backups_created:
        return InspectorDecision.NOOP

    # Backups exist but no replacements -> suspicious state -> rollback
    if not file_replaced and backups_created:
        return InspectorDecision.ROLLBACK

    # Now we have at least one file_replaced entry
    replaced_files = {os.path.basename(e.get("file") or "") for e in file_replaced}
    backup_files = {os.path.basename(e.get("file") or "") for e in backups_created}

    # If any replaced file is missing a backup -> roll back
    missing_backups = [f for f in replaced_files if f not in backup_files]
    if missing_backups:
        return InspectorDecision.ROLLBACK

    # All replaced files have backups -> safe to resume commit
    return InspectorDecision.RESUME_COMMIT


def _copy_backup_to_target(backup_path: str, target_path: str) -> bool:
    try:
        if not os.path.exists(backup_path):
            return False
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        shutil.copy2(backup_path, target_path)
        return True
    except Exception:
        logger.exception("restore_failed backup=%s target=%s", backup_path, target_path)
        return False


def _remove_target_path(target_path: str) -> bool:
    try:
        if os.path.exists(target_path):
            os.remove(target_path)
        return True
    except Exception:
        logger.exception("remove_failed target=%s", target_path)
        return False


def perform_rollback(event_id: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    backups = _find_entries_by_action(entries, "backup_created")
    replaced = _find_entries_by_action(entries, "file_replaced")

    backup_map = {b.get("file"): b for b in backups}

    restored = []
    removed = []
    missing = []

    for r in replaced:
        fname = r.get("file")
        target = r.get("path")
        b = backup_map.get(fname)

        if b and os.path.exists(b.get("backup", "")):
            ok = _copy_backup_to_target(b.get("backup"), target)
            if ok:
                restored.append(fname)
            else:
                missing.append(fname)
        else:
            ok = _remove_target_path(target)
            if ok:
                removed.append(fname)
            else:
                missing.append(fname)

    if missing:
        raise RuntimeError(f"rollback incomplete for {event_id}: {missing}")

    # Only mark a completed rollback; failed operations must remain retryable.
    try:
        journal.append_entry(event_id, "rollback", {"ts": "inspector"})
    except Exception:
        logger.exception("journal_append_failed event_id=%s", event_id)
        raise

    return {"restored": restored, "removed": removed, "missing": missing}


def perform_resume_commit(event_id: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    replaced = _find_entries_by_action(entries, "file_replaced")
    applied_files = [r.get("file") for r in replaced]

    try:
        journal.append_entry(event_id, "commit", {"applied_files": applied_files, "ts": "inspector"})
    except Exception:
        logger.exception("journal_append_failed event_id=%s", event_id)
        return {"ok": False}

    return {"applied_files": applied_files, "ok": True}


def inspect_and_recover_one(journal_path: str) -> Dict[str, Any]:
    event_id = _journal_event_id_from_path(journal_path)
    entries = _read_jsonl(journal_path)
    decision = decide_action_for_journal(entries)

    logger.info("inspector_decision event_id=%s decision=%s", event_id, decision)

    if decision == InspectorDecision.NOOP:
        return {"event_id": event_id, "action": "noop"}

    if decision == InspectorDecision.ROLLBACK:
        result = perform_rollback(event_id, entries)
        return {"event_id": event_id, "action": "rollback", "result": result}

    if decision == InspectorDecision.RESUME_COMMIT:
        result = perform_resume_commit(event_id, entries)
        if not result["ok"]:
            raise RuntimeError(f"commit marker write failed for {event_id}")
        return {"event_id": event_id, "action": "resume_commit", "result": result}

    return {"event_id": event_id, "action": "noop"}


def scan_and_recover_all() -> List[Dict[str, Any]]:
    out = []
    for jp in _iter_journal_files():
        try:
            rec = inspect_and_recover_one(jp)
            out.append(rec)
        except Exception as exc:
            logger.exception("inspect_failed journal=%s", jp)
            out.append({"event_id": _journal_event_id_from_path(jp), "action": "error", "error": str(exc)})
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect journals and attempt resume/rollback")
    parser.add_argument("--report", action="store_true", help="Just show decisions, make no changes")
    args = parser.parse_args()

    decisions = []
    for jp in _iter_journal_files():
        entries = _read_jsonl(jp)
        decision = decide_action_for_journal(entries)
        event_id = _journal_event_id_from_path(jp)
        print(f"{event_id}: {decision}")
        decisions.append((event_id, decision))

    if args.report:
        print("Report-only mode. No changes made.")
    else:
        print("Running inspector:")
        results = scan_and_recover_all()
        for r in results:
            print(r)
