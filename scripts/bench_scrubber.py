#!/usr/bin/env python3
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

import time
import asyncio
from services.file_extractor import extract_files_from_event

class DummyEvent:
    def __init__(self, repo_full_name, payload):
        self.repo_full_name = repo_full_name
        self.payload = payload

async def bench(n=100):
    evt = DummyEvent(
        repo_full_name="me/repo",
        payload={"files": {"a.py": "AKIA" * 50}} # type: ignore[arg-type]
    )

    t0 = time.time()
    for _ in range(n): # type: ignore[arg-type]
        await extract_files_from_event(evt, return_findings=True)
    dt = time.time() - t0

    print("=== Benchmark ===")
    print(f"Iterations: {n}")
    print(f"Total time: {dt:.3f}s")
    print(f"Avg per call: {dt/n:.6f}s")
    print(f"Throughput: {n/dt:.2f} operations/sec")


if __name__ == "__main__":
    asyncio.run(bench())