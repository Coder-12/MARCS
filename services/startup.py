# services/startup.py
from __future__ import annotations
import os
import logging
from typing import List, Dict, Any

from services import journal_inspector

logger = logging.getLogger("services.startup")

# Control via env:
# - MACRS_RUN_STARTUP_INSPECTOR=0  -> disable
# - MACRS_INSPECTOR_DRY_RUN=1     -> dry-run (no journal mutation)
INSPECTOR_ENABLED = os.environ.get("MACRS_RUN_STARTUP_INSPECTOR", "1") == "1"
INSPECTOR_DRY = os.environ.get("MACRS_INSPECTOR_DRY_RUN", "0") == "1"

_last_startup_decisions: List[Dict[str, Any]] = []


def run_startup_inspector() -> None:
    """
    Run the journal inspector once at startup. Safe: NOOP if disabled.
    If INSPECTOR_DRY is True, we compute decisions but do not mutate journals.
    Dry-run journal errors are logged and reported individually; valid journals
    are still inspected and startup continues without recovering corrupt history.
    Results are stored in-memory (get_last_startup_decisions) for health endpoint reporting.
    """
    global _last_startup_decisions

    if not INSPECTOR_ENABLED:
        logger.info("Startup inspector disabled via MACRS_RUN_STARTUP_INSPECTOR=0")
        _last_startup_decisions = []
        return

    logger.info("Running startup journal inspector (dry=%s)", INSPECTOR_DRY)
    _last_startup_decisions = []

    if INSPECTOR_DRY:
        # safe dry run: inspect and decide, but do not mutate journals
        for jp in journal_inspector._iter_journal_files():
            try:
                entries = journal_inspector._read_jsonl(jp)
                decision = journal_inspector.decide_action_for_journal(entries)
                _last_startup_decisions.append({"journal": jp, "decision": decision})
            except Exception as exc:
                logger.exception("startup_journal_inspection_failed journal=%s", jp)
                _last_startup_decisions.append({"journal": jp, "decision": "error", "error": str(exc)})
        return

    # Full recover (may append commit/rollback entries)
    try:
        decisions = journal_inspector.scan_and_recover_all()
        _last_startup_decisions = decisions
    except Exception:
        logger.exception("startup_inspector_failed")
        _last_startup_decisions = [{"error": "startup_inspector_failed"}]


def get_last_startup_decisions() -> List[Dict[str, Any]]:
    return _last_startup_decisions
