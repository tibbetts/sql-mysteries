"""CLI: python -m sqlmystery make|verify|grade|batch"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import TIERS
from .generate import make
from .grade import grade
from .verify import verify


def parse_seeds(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def instance_dir(root: Path, tier: str, seed: int) -> Path:
    return Path(root) / tier / f"seed_{seed:04d}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sqlmystery")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="generate one instance")
    m.add_argument("--seed", type=int, required=True)
    m.add_argument("--tier", choices=TIERS, default="medium")
    m.add_argument("--persons", type=int, help="override population size")
    m.add_argument("--out", type=Path, required=True)

    b = sub.add_parser("batch", help="generate many instances under OUT/<tier>/seed_NNNN")
    b.add_argument("--seeds", default="0-9", help="e.g. 0-99 or 1,5,9")
    b.add_argument("--tier", choices=TIERS, default="medium")
    b.add_argument("--persons", type=int)
    b.add_argument("--out", type=Path, required=True)

    v = sub.add_parser("verify", help="re-check an instance directory")
    v.add_argument("dir", type=Path)

    g = sub.add_parser("grade", help="grade a submission json against answer.json")
    g.add_argument("answer", type=Path)
    g.add_argument("submission", type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "make":
        tier = TIERS[args.tier]
        if args.persons:
            tier = tier.scaled(args.persons)
        t = time.time()
        out = make(args.seed, tier, args.out)
        print(f"wrote {out} in {time.time() - t:.1f}s")
    elif args.cmd == "batch":
        tier = TIERS[args.tier]
        if args.persons:
            tier = tier.scaled(args.persons)
        for seed in parse_seeds(args.seeds):
            t = time.time()
            out = make(seed, tier, instance_dir(args.out, args.tier, seed))
            print(f"wrote {out} in {time.time() - t:.1f}s", flush=True)
    elif args.cmd == "verify":
        verify(args.dir)
        print("ok")
    elif args.cmd == "grade":
        result = grade(json.loads(args.answer.read_text()), json.loads(args.submission.read_text()))
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
