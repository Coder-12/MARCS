# tools/generate_golden.py
"""CLI wrapper around GoldenFactory.

Example:
  python tools/generate_golden.py --version v1 --out data/golden --seed 42 --style 5 --security 5 --mix 2 --empty 1 --big 1
"""
import os
import sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import argparse
from tools.golden_factory import GoldenFactory


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v1")
    p.add_argument("--out", default=None)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--style", type=int, default=5)
    p.add_argument("--security", type=int, default=5)
    p.add_argument("--mix", type=int, default=2)
    p.add_argument("--empty", type=int, default=1)
    p.add_argument("--big", type=int, default=1)

    args = p.parse_args()

    gf = GoldenFactory(version=args.version, out_dir=args.out, seed=args.seed)
    counts = {
        "style": args.style,
        "security": args.security,
        "mix": args.mix,
        "empty": args.empty,
        "big": args.big,
    }
    paths = gf.generate(counts)
    print(f"Wrote {len(paths)} cases to {gf.target_dir}")


if __name__ == "__main__":
    main()