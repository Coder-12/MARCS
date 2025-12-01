#!/usr/bin/env python3
"""
scripts/run_inspector.py --dry-run
Run the journal inspector from the CLI (dry-run or full).
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

from services import journal_inspector
import argparse
import json

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.dry_run:
        out = []
        for jp in journal_inspector._iter_journal_files():
            entries = journal_inspector._read_jsonl(jp)
            dec = journal_inspector.decide_action_for_journal(entries)
            out.append({"journal": jp, "decision": dec})
        print(json.dumps(out, indent=2))
        return

    res = journal_inspector.scan_and_recover_all()
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()