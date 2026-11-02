"""Phase 1 baselines: default<O0/O1/O2/O3/Os> for every benchmark.

Each level goes through our pipeline (bitcode -> opt -> llc -O3 -> link), so only
the middle end differs, exactly as for tuned candidates. Every build must pass
the correctness gate (D8) before it is timed. The levels are timed interleaved;
speedup is reported against default<O3> with a bootstrap CI.
Writes results/<out>.csv and results/<out>_samples.json.
"""

import argparse
import csv
import json
import sys

import numpy as np

from o4_llvm.bench import correctness
from o4_llvm.bench.machine import preflight_warnings
from o4_llvm.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from o4_llvm.bench.timing import cv, run_interleaved, speedup_ci
from o4_llvm.compiler.toolchain import Toolchain
from o4_llvm.config import load_config, repo_path

LEVELS = ["O0", "O1", "O2", "O3", "Os"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default="baselines")
    args = ap.parse_args()

    cfg = load_config(args.config)
    t = cfg["timing"]
    tc = Toolchain(cfg)
    tc.check_version()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)

    bin_dir = repo_path(cfg, "build_dir") / "baselines"
    results_dir = repo_path(cfg, "results_dir")
    results_dir.mkdir(parents=True, exist_ok=True)

    rows, raw = [], {}
    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    for bench in benches:
        bcs = {}
        for variant in ("time", "check"):
            bcs[variant] = bitcode_path(cfg, bench, variant)
            if not bcs[variant].exists():
                build_bitcode(tc, cfg, bench, variant)

        builds, gates = {}, {}
        for lvl in LEVELS:
            pipeline = f"default<{lvl}>"
            builds[lvl] = tc.build(bcs["time"], pipeline, bin_dir / f"{bench.name}.{lvl}")
            chk = tc.build(bcs["check"], pipeline, bin_dir / f"{bench.name}.{lvl}.check")
            gates[lvl] = correctness.check(tc, cfg, bench, chk.binary)
            if not gates[lvl].ok:
                print(f"{bench.name} {lvl}: gate {gates[lvl].status}: {gates[lvl].detail}")

        timed = [l for l in LEVELS if gates[l].ok]
        s = run_interleaved({l: builds[l].binary for l in timed}, 1, args.reps,
                            cfg["limits"]["run_timeout_s"])
        raw[bench.name] = s
        line = [f"{bench.name:<15}"]
        for lvl in LEVELS:
            b = builds[lvl]
            row = {"benchmark": bench.name, "level": lvl, "gate": gates[lvl].status,
                   "gate_max_rel_err": gates[lvl].max_rel_err,
                   "compile_s": b.compile_s, "text_size": b.text_size}
            if lvl in s:
                sp = speedup_ci(s["O3"], s[lvl], t["bootstrap_samples"], cfg["seeds"][0])
                row.update(median_s=float(np.median(s[lvl])), cv=cv(s[lvl]),
                           speedup_vs_O3=sp.ratio, ci_lo=sp.ci_lo, ci_hi=sp.ci_hi)
                line.append(f"{lvl} {row['median_s']:7.3f}s ({sp.ratio:5.3f}x)")
            else:
                line.append(f"{lvl} {gates[lvl].status}")
            rows.append(row)
        print("  ".join(line), flush=True)

    out = results_dir / f"{args.out}.csv"
    (results_dir / f"{args.out}_samples.json").write_text(json.dumps(raw, indent=1))
    fields = ["benchmark", "level", "gate", "gate_max_rel_err", "median_s", "cv",
              "speedup_vs_O3", "ci_lo", "ci_hi", "compile_s", "text_size"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\nGeomean speedup vs default<O3> over {len(benches)} benchmarks:")
    for lvl in LEVELS:
        sp = [r["speedup_vs_O3"] for r in rows if r["level"] == lvl and "speedup_vs_O3" in r]
        if sp:
            print(f"  {lvl}: {float(np.exp(np.mean(np.log(sp)))):.3f}x  (n={len(sp)})")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
