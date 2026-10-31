"""How much does each benchmark's runtime depend on array layout? (D7)

Times each default<O3> pipeline binary under several LLVM04_LAYOUT_SALT values
(benchmarks/runtime/placement.c) and reports the spread of the per-salt medians.
A tuning win smaller than its benchmark's spread could be a layout effect, not a
real improvement.
Writes results/<out>.csv and results/<out>_samples.json.
"""

import argparse
import csv
import json
import sys

import numpy as np

from llvm04.bench.machine import preflight_warnings
from llvm04.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from llvm04.bench.timing import cv, time_layouts
from llvm04.compiler.toolchain import Toolchain
from llvm04.config import load_config, repo_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--salts", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--reps", type=int, default=5, help="runs per salt")
    ap.add_argument("--out", default="layout_sensitivity")
    args = ap.parse_args()

    cfg = load_config(args.config)
    tc = Toolchain(cfg)
    tc.check_version()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)

    bin_dir = repo_path(cfg, "build_dir") / "layout"
    results_dir = repo_path(cfg, "results_dir")
    results_dir.mkdir(parents=True, exist_ok=True)
    timeout = cfg["limits"]["run_timeout_s"]

    rows, raw = [], {}
    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    for bench in benches:
        bc = bitcode_path(cfg, bench, "time")
        if not bc.exists():
            build_bitcode(tc, cfg, bench, "time")
        binary = tc.build(bc, "default<O3>", bin_dir / f"{bench.name}.pipeline_O3").binary

        time_layouts(binary, args.salts, 1, timeout)  # warm-up
        s = time_layouts(binary, args.salts, args.reps, timeout)
        raw[bench.name] = {str(k): v for k, v in s.items()}
        medians = {k: float(np.median(v)) for k, v in s.items()}
        lo, hi = min(medians.values()), max(medians.values())
        row = {
            "benchmark": bench.name,
            **{f"salt{k}_median_s": m for k, m in medians.items()},
            "spread": hi / lo - 1,
            "worst_cv": max(cv(v) for v in s.values()),
        }
        rows.append(row)
        print(f"{bench.name:<16} " + "  ".join(f"s{k} {m:7.3f}s" for k, m in medians.items())
              + f"  spread {row['spread']:6.2%}  (within-salt cv <= {row['worst_cv']:.2%})")

    out = results_dir / f"{args.out}.csv"
    (results_dir / f"{args.out}_samples.json").write_text(json.dumps(raw, indent=1))
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    sensitive = [r["benchmark"] for r in rows if r["spread"] > cfg["timing"]["max_cv"]]
    print(f"\n{len(rows) - len(sensitive)}/{len(rows)} benchmarks have layout spread "
          f"<= {cfg['timing']['max_cv']:.0%} -> {out}")
    if sensitive:
        print("Layout-sensitive:", ", ".join(sensitive))
    return 0


if __name__ == "__main__":
    sys.exit(main())
