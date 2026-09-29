#!/usr/bin/env python3
import os
import sys
import argparse
import asyncio
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT) # type: ignore[arg-type]

from services.file_extractor import extract_files_from_event


class DummyEvent:
    def __init__(self, repo_full_name, payload):
        self.repo_full_name = repo_full_name
        self.payload = payload


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--files", nargs="+", required=True)

    # NEW FLAGS:
    parser.add_argument("--show", action="store_true",
                        help="Show sanitized output for each file")
    parser.add_argument("--verbose", action="store_true",
                        help="Show findings (exact scrubber detections)")
    parser.add_argument("--direct", action="store_true")

    args = parser.parse_args()

    if args.direct:
        evt = DummyEvent(
            repo_full_name=args.repo,
            payload={"files": {f: open(f).read() for f in args.files}}
        )
    else:
        evt = DummyEvent(
            repo_full_name=args.repo,
            payload={
                "commits": [{"added": [], "modified": args.files}],
                "ref": f"refs/heads/{args.branch}",
            },
        )

    t0 = time.time()
    out = await extract_files_from_event(evt, return_findings=True)
    dt = time.time() - t0

    sanitized = out.get("files", {})
    findings = out.get("findings", {})

    print("=== Stress Test ===")
    print(f"Repo: {args.repo}")
    print(f"Files processed: {len(sanitized)}")
    print(f"Findings total: {sum(len(v) for v in findings.values())}")
    print(f"Time: {dt:.2f}s")

    # NEW: Print findings breakdown
    if args.verbose:
        print("\n--- Findings Detail ---")
        for fn, fs in findings.items():
            print(f"\n[{fn}]")
            for f in fs:
                print(f"  - type={f['type']} sample={f['sample']} hash={f['hash']}")

    # NEW: Print sanitized text
    if args.show:
        print("\n--- Sanitized Output ---")
        for fn, content in sanitized.items():
            print(f"\n[{fn}]\n{content}\n")


if __name__ == "__main__":
    asyncio.run(main())