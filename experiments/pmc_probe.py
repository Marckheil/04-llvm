"""Hardware counters behind the two Phase 1 wins (docs/decisions.md D12).

Needs the root-owned launcher experiments/counters/pmc_run.c installed at PMC_RUN
with a NOPASSWD sudoers rule for it (see D12). The launcher programs the PMU, runs
the binary as the calling user, and prints system-wide event deltas.

Binaries (built earlier by syr2k_store_probe.py and gep_opt_sweep.py):
  syr2k      O3, O3+nop, O3+store, win, win-store
  jacobi-2d  O3, O3+scofg
Cases with "pipelines" are built here instead, into build/pmc/<case>/ (D14: the
minimal agent pipelines from experiments/pipeline_ablation.py):
  jacobi-2d-lean  O3, O3+scofg, lean (sroa indvars loop-vectorize)
  floyd-warshall  O3, lean (8 passes, below)
Per event group and rep, the binaries run interleaved (ABBA), and a control
`/bin/sleep T` (T = that benchmark's O3 run time) measures the background rate,
which is subtracted per second of each run. Counts are then divided by the number
of hot-loop iterations: syr2k's j loop runs M * N(N+1)/2 times, jacobi-2d updates
2 * TSTEPS * (N-2)^2 points (LARGE dataset).
Writes results/<out>.csv (median corrected count per binary and event) and
results/<out>_raw.jsonl (every run).
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import defaultdict

import numpy as np

from llvm04.config import load_config, repo_path

PMC_RUN = "/usr/local/libexec/llvm04/pmc_run"
FIXED = ["FIXED_CYCLES", "FIXED_INSTRUCTIONS"]
# Counter masks on the M2 (a15.plist): 128 = counter 7 only, 224 = counters 5-7,
# none = any of 2-9. Events are added in order and placed greedily, so the
# constrained ones come first in each group.
GROUPS = {
    "pipeline": ["RETIRE_UOP", "ST_MEM_ORDER_VIOL_LD_NONSPEC", "MAP_STALL",
                 "MAP_STALL_DISPATCH", "MAP_REWIND", "MAP_DISPATCH_BUBBLE", "SCHEDULE_EMPTY",
                 "FLUSH_RESTART_OTHER_NONSPEC"],
    "memory": ["BRANCH_MISPRED_NONSPEC", "L1D_CACHE_MISS_LD", "L1D_CACHE_MISS_ST",
               "LD_UNIT_UOP", "ST_UNIT_UOP", "LDST_X64_UOP", "L1D_TLB_MISS", "MAP_LDST_UOP"],
    "mix": ["INST_LDST", "INST_INT_LD", "INST_SIMD_LD", "MAP_INT_UOP", "MAP_SIMD_UOP",
            "L1D_CACHE_WRITEBACK", "FETCH_RESTART"],
}
CASES = {
    "syr2k": {"dir": "syr2k_store_probe", "bins": ["O3", "O3+nop", "O3+store", "win",
                                                   "win-store"],
              "iters": 1000 * 1200 * 1201 // 2},
    "jacobi-2d": {"dir": "gep_opt/jacobi-2d", "bins": ["O3", "O3+scofg"],
                  "iters": 2 * 500 * 1298 * 1298},
    "jacobi-2d-lean": {"bench": "jacobi-2d", "iters": 2 * 500 * 1298 * 1298, "pipelines": {
        "O3": "default<O3>",
        "O3+scofg": ("O3", ["separate-const-offset-from-gep"]),
        "lean": ("none", ["sroa", "indvars", "loop-vectorize"])}},
    "floyd-warshall": {"bench": "floyd-warshall", "iters": 2800 ** 3, "pipelines": {
        "O3": "default<O3>",
        "lean": ("none", ["sroa", "early-cse<memssa>", "simplifycfg", "loop-rotate", "licm",
                          "loop-unroll", "instcombine", "licm"])}},
}


def build_case(cfg: dict, name: str, spec: dict) -> dict[str, str]:
    from llvm04.bench.polybench import bitcode_path, list_benchmarks
    from llvm04.compiler.pipeline import load_passes, render
    from llvm04.compiler.toolchain import Toolchain
    from llvm04.tools.service import BASES

    tc, known = Toolchain(cfg), load_passes()
    bench = next(b for b in list_benchmarks(cfg) if b.name == spec["bench"])
    bins = {}
    for b, p in spec["pipelines"].items():
        if not isinstance(p, str):
            p = ",".join(x for x in (BASES[p[0]], render(p[1], known)) if x)
        out = tc.build(bitcode_path(cfg, bench, "time"), p,
                       repo_path(cfg, "build_dir") / "pmc" / name / b)
        bins[b] = str(out.binary)
    return bins


def pmc(events: list[str], cmd: list[str]) -> tuple[dict[str, int], float]:
    t0 = time.perf_counter()
    res = subprocess.run(["sudo", "-n", PMC_RUN, ",".join(events), "--", *cmd],
                         capture_output=True, text=True, timeout=300)
    elapsed = time.perf_counter() - t0
    if res.returncode != 0:
        raise RuntimeError(f"pmc_run failed ({res.returncode}): {res.stderr.strip()}")
    counts = {}
    for line in res.stdout.splitlines():
        if line.startswith("pmc "):
            _, name, value = line.split()
            counts[name] = int(value)
    return counts, elapsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--case", nargs="*", choices=list(CASES), help="default: all")
    ap.add_argument("--out", default="pmc_probe")
    args = ap.parse_args()
    cfg = load_config()
    build, results = repo_path(cfg, "build_dir"), repo_path(cfg, "results_dir")
    raw_f = open(results / f"{args.out}_raw.jsonl", "w")
    rows = []
    for case, spec in CASES.items():
        if args.case and case not in args.case:
            continue
        bins = (build_case(cfg, case, spec) if "pipelines" in spec
                else {b: str(build / spec["dir"] / b) for b in spec["bins"]})
        # Warm up and size the control. (A fixed-counters-only config is rejected.)
        _, t_o3 = pmc(FIXED + ["MAP_STALL"], [bins["O3"]])
        _, t_o3 = pmc(FIXED + ["MAP_STALL"], [bins["O3"]])
        control = ["/bin/sleep", f"{t_o3:.3f}"]
        corrected = defaultdict(lambda: defaultdict(list))  # bin -> event -> [counts]
        for group, events in GROUPS.items():
            evs = FIXED + events
            for rep in range(args.reps):
                order = list(bins) if rep % 2 == 0 else list(bins)[::-1]
                bg, bg_t = pmc(evs, control)
                raw_f.write(json.dumps({"case": case, "group": group, "rep": rep,
                                        "bin": "control", "elapsed": bg_t, **bg}) + "\n")
                for b in order:
                    c, t = pmc(evs, [bins[b]])
                    raw_f.write(json.dumps({"case": case, "group": group, "rep": rep,
                                            "bin": b, "elapsed": t, **c}) + "\n")
                    for e in evs:
                        corrected[b][e].append(c[e] - bg[e] / bg_t * t)
                raw_f.flush()
        print(f"\n{case}: per hot-loop iteration, background-corrected, median of "
              f"{args.reps} reps x {len(GROUPS)} groups (fixed counters)")
        events = FIXED + [e for g in GROUPS.values() for e in g]
        print(f"  {'event':<30}" + "".join(f"{b:>12}" for b in bins))
        for e in events:
            meds = {b: float(np.median(corrected[b][e])) for b in bins}
            line = f"  {e:<30}" + "".join(f"{meds[b] / spec['iters']:>12.4f}" for b in bins)
            print(line, flush=True)
            for b in bins:
                v = np.asarray(corrected[b][e])
                rows.append({"case": case, "binary": b, "event": e, "median": meds[b],
                             "per_iter": meds[b] / spec["iters"],
                             "min": float(v.min()), "max": float(v.max()), "n": len(v)})
    with open(results / f"{args.out}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {results / args.out}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
