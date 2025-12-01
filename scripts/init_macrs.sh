#!/usr/bin/env bash
#
# Initialize MARCS directory structure.
# Safe: idempotent, creates only missing directories, never deletes.
# Works on macOS & Linux.
#

set -e

echo ""
echo "=============================================="
echo "     Initializing MARCS Directory Structure    "
echo "=============================================="
echo ""

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo $ROOT

# Required MARCS directories
DIRS=(
  "journals"
  "backups"
  "artifacts"
  "logs"
)

for d in "${DIRS[@]}"; do
  TARGET="$ROOT/$d"
  if [[ ! -d "$TARGET" ]]; then
    echo "Creating directory: $TARGET"
    mkdir -p "$TARGET"
  else
    echo "Directory exists:  $TARGET"
  fi

  # Ensure .gitkeep exists
  if [[ ! -f "$TARGET/.gitkeep" ]]; then
    echo "Creating .gitkeep in $TARGET"
    touch "$TARGET/.gitkeep"
  fi

  echo ""
done

# Optional: create demo folders under artifacts
if [[ ! -d "$ROOT/artifacts/demo" ]]; then
  echo "Creating artifacts/demo directory"
  mkdir -p "$ROOT/artifacts/demo"
  touch "$ROOT/artifacts/demo/.gitkeep"
  echo ""
fi

echo "Initialization complete."
echo "MARCS directories are ready."
echo ""
echo "Structure:"
echo ""
tree "$ROOT" 2>/dev/null || ls -R "$ROOT"
echo ""
echo "=============================================="
echo "            MARCS INIT DONE ✔️"
echo "=============================================="