"""Phase 0 exit check: is timing reproducible, and does our O3 pipeline match clang -O3?

For each benchmark, builds
  pipeline_O3: bitcode -> opt -passes=default<O3> -> llc -> link  (what we tune against)
  clang_O3:    clang -O3 straight from source                     (the real -O3)
times both interleaved, and reports noise (CV) and parity (speedup CI).
Writes results/phase0_noise.csv.
"""

import argparse
import csv
import sys

import numpy as np

from passpilot.bench.machine import preflight_warnings
from passpilot.bench.polybench import (bitcode_path, build_bitcode, build_from_source,
                                       list_benchmarks)
from passpilot.bench.timing import cv, run_interleaved, speedup_ci
from passpilot.compiler.toolchain import Toolchain
from passpilot.config import load_config, repo_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--reps", type=int, help="override timing.reps")
    args = ap.parse_args()

    cfg = load_config(args.config)
    t = cfg["timing"]
    reps = args.reps or t["reps"]
    tc = Toolchain(cfg)
    tc.check_version()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)

    bin_dir = repo_path(cfg, "build_dir") / "phase0"
    results_dir = repo_path(cfg, "results_dir")
    results_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    for bench in benches:
        bc = bitcode_path(cfg, bench, "time")
        if not bc.exists():
            build_bitcode(tc, cfg, bench, "time")
        pipe = tc.build(bc, "default<O3>", bin_dir / f"{bench.name}.pipeline_O3")
        ref = build_from_source(tc, cfg, bench, "time", "-O3", bin_dir / f"{bench.name}.clang_O3")

        s = run_interleaved({"pipeline": pipe.binary, "clang": ref}, t["warmup"], reps,
                            cfg["limits"]["run_timeout_s"])
        parity = speedup_ci(s["clang"], s["pipeline"], t["bootstrap_samples"], cfg["seeds"][0])
        row = {
            "benchmark": bench.name,
            "pipeline_median_s": float(np.median(s["pipeline"])),
            "pipeline_cv": cv(s["pipeline"]),
            "clang_median_s": float(np.median(s["clang"])),
            "clang_cv": cv(s["clang"]),
            "parity_ratio": parity.ratio,
            "parity_ci_lo": parity.ci_lo,
            "parity_ci_hi": parity.ci_hi,
            "compile_s": pipe.compile_s,
        }
        row["noise_ok"] = row["pipeline_cv"] <= t["max_cv"]
        row["runtime_ok"] = row["pipeline_median_s"] >= t["min_runtime_s"]
        row["parity_ok"] = abs(parity.ratio - 1) <= t["max_cv"] or not parity.significant
        rows.append(row)
        flags = " ".join(k.removesuffix("_ok") for k in ("noise_ok", "runtime_ok", "parity_ok")
                         if not row[k])
        print(f"{bench.name:<16} {row['pipeline_median_s']:8.3f}s  cv {row['pipeline_cv']:6.2%}"
              f"  parity {parity.ratio:.3f} [{parity.ci_lo:.3f}, {parity.ci_hi:.3f}]"
              f"  {'OK' if not flags else 'CHECK: ' + flags}")

    out = results_dir / "phase0_noise.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    bad = [r["benchmark"] for r in rows
           if not (r["noise_ok"] and r["runtime_ok"] and r["parity_ok"])]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} benchmarks pass Phase 0 checks -> {out}")
    if bad:
        print("Needs attention:", ", ".join(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
