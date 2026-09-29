# scripts/migrate.py
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import sqlite3
from core.config import SQLITE_PATH
MIGRATION = os.path.join(os.path.dirname(__file__), "..", "migrations", "create_tables.sql")

def run():
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    with open(MIGRATION, "r", encoding="utf-8") as fh:
        sql = fh.read()
    conn = sqlite3.connect(SQLITE_PATH)
    conn.executescript(sql)
    conn.close()
    print("migrations applied to", SQLITE_PATH)

if __name__ == "__main__":
    run()