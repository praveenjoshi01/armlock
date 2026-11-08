#!/usr/bin/env python
"""Dry-run check of one Claude response file against the simulator. Writes nothing.

  .venv/bin/python scripts/check_responses.py results/run_005/claude_gen/responses/resp_00200.txt
  (top-up pairs: add --version 2 --offset 10000)

Prints how many pairs the environment accepts and, for each rejected one, the idx, which side (S or R) and the
simulator's own reason. Fix the lines and run it again until it reports 0 rejected.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from data.claude_gen import ClaudeRLCDGenerator, load_responses  # noqa: E402
from harness.runner import load_all_scenarios  # noqa: E402


def main(path: str, version: int = 1, offset: int = 0) -> int:
    p = Path(path)
    tmp = p.parent / f".check_{p.name}"
    tmp_dir = p.parent / f".check_dir_{p.stem}"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "r.txt").write_text(p.read_text())
    resp = load_responses(tmp_dir)
    (tmp_dir / "r.txt").unlink()
    tmp_dir.rmdir()
    gen = ClaudeRLCDGenerator(load_all_scenarios(), resp, version=version, idx_offset=offset)
    ok, bad = 0, []
    for idx in sorted(resp):
        pair = gen.make_claude_pair(idx - offset)
        if "records" in pair:
            ok += 1
        else:
            bad.append((idx, pair["failed"]))
    print(f"{p.name}: {len(resp)} lines read, {ok} accepted, {len(bad)} rejected")
    for idx, why in bad:
        print(f"  idx {idx}: {why[:260]}")
    return 1 if bad else 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path"); ap.add_argument("--version", type=int, default=1); ap.add_argument("--offset", type=int, default=0)
    a = ap.parse_args()
    sys.exit(main(a.path, a.version, a.offset))
