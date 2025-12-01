#!/usr/bin/env bash
# scripts/run_inspector.sh
set -e
if [[ "$1" == "--dry-run" ]]; then
  python3 scripts/run_inspector.py --dry-run
else
  python3 scripts/run_inspector.py
fi