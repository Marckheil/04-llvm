"""Phase 1 random-search baseline: the bar every later method must beat.

Per benchmark and seed: spend the compile budget (episode.compile_budget) on
random pipelines from configs/passes.yaml (search.modes: from scratch, or
default<O3> plus a random suffix), keep the fastest correct one, then
  1. confirm it against default<O3> (search.confirm_reps interleaved runs, bootstrap CI)
  2. if the CI says it's faster, re-time both under every layout salt (D7);
     it counts as a WIN only if it is faster under all of them.
Writes results/<out>.csv (one row per benchmark x seed, appended as it goes),
results/<out>_candidates.jsonl (every candidate, for sample-efficiency curves) and
results/<out>_timing.jsonl (raw confirmation and layout-check runs).
"""

import argparse
import csv
import json
import sys
from collections import Counter

import numpy as np

from llvm04.bench.machine import preflight_warnings
from llvm04.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from llvm04 import proc
from llvm04.bench.timing import parse_polybench_time, run_interleaved, speedup_ci, time_once
from llvm04.compiler.pipeline import load_passes
from llvm04.compiler.toolchain import Toolchain
from llvm04.config import load_config, repo_path
from llvm04.search.candidate import OK
from llvm04.search.random_search import random_search

FIELDS = ["benchmark", "mode", "seed", "verdict", "speedup_vs_O3", "ci_lo", "ci_hi",
          "layout_min_ratio", "best_screen_s", "O3_median_s", "best_len", "best_compile_s",
          "O3_compile_s", "best_text_size", "O3_text_size", "n_ok", "n_slow", "n_miscompile", "n_crash", "n_compile_error",
          "n_timeout", "best_pipeline"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--seeds", type=int, nargs="*", help="default: config seeds")
    ap.add_argument("--budget", type=int, help="override episode.compile_budget")
    ap.add_argument("--mode", default="o3_suffix", help="a key of search.modes")
    ap.add_argument("--out", help="default: random_search_<mode>")
    args = ap.parse_args()

    cfg = load_config(args.config)
    s, t = cfg["search"], cfg["timing"]
    args.out = args.out or f"random_search_{args.mode}"
    seeds = args.seeds if args.seeds is not None else cfg["seeds"]
    budget = args.budget or cfg["episode"]["compile_budget"]
    timeout = cfg["limits"]["run_timeout_s"]
    tc = Toolchain(cfg)
    tc.check_version()
    passes = load_passes()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)

    results_dir = repo_path(cfg, "results_dir")
    results_dir.mkdir(parents=True, exist_ok=True)
    out_csv = results_dir / f"{args.out}.csv"
    new_file = not out_csv.exists()
    csv_f = open(out_csv, "a", newline="")
    writer = csv.DictWriter(csv_f, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()
    cand_f = open(results_dir / f"{args.out}_candidates.jsonl", "a")
    timing_f = open(results_dir / f"{args.out}_timing.jsonl", "a")

    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    for bench in benches:
        for variant in ("time", "check"):
            if not bitcode_path(cfg, bench, variant).exists():
                build_bitcode(tc, cfg, bench, variant)
        work = repo_path(cfg, "build_dir") / "search" / bench.name
        o3 = tc.build(bitcode_path(cfg, bench, "time"), "default<O3>", work / "O3")
        time_once(o3.binary, timeout)
        o3_runs = [proc.run([o3.binary], timeout) for _ in range(3)]
        o3_med = float(np.median([parse_polybench_time(r.stdout) for r in o3_runs]))
        # The cap kills whole processes, so it must scale the whole-process time:
        # some benchmarks (cholesky, lu, ludcmp) spend longer in init than in the kernel.
        slow_cap = s["slow_cap"] * float(np.median([r.elapsed_s for r in o3_runs]))

        for seed in seeds:
            counts = Counter()

            def log(i, names, c, seed=seed, counts=counts):
                slow = c.status == OK and c.screen_best is None
                counts["slow" if slow else c.status] += 1
                cand_f.write(json.dumps({
                    "benchmark": bench.name, "mode": args.mode, "seed": seed, "i": i,
                    "passes": names,
                    "status": c.status, "slow": slow, "screen_s": c.screen_s,
                    "compile_s": c.compile_s, "text_size": c.text_size,
                    "gate_max_rel_err": c.gate_max_rel_err, "detail": c.detail}) + "\n")
                cand_f.flush()
                if c.status not in (OK,):
                    print(f"  {bench.name} s{seed} c{i}: {c.status} {c.detail[:120]}", flush=True)

            names, best = random_search(tc, cfg, bench, passes, args.mode, seed, budget,
                                        slow_cap, work / args.mode / f"s{seed}",
                                        on_candidate=log)
            row = {"benchmark": bench.name, "mode": args.mode, "seed": seed,
                   "O3_median_s": o3_med,
                   "O3_compile_s": o3.compile_s, "O3_text_size": o3.text_size,
                   **{f"n_{k.lower()}": counts.get(k, 0) for k in
                      ("OK", "slow", "MISCOMPILE", "CRASH", "COMPILE_ERROR", "TIMEOUT")}}
            row["n_ok"] = counts.get("OK", 0)
            if best is None:
                row["verdict"] = "NO_CANDIDATE"
            else:
                samp = run_interleaved({"best": best.binary, "O3": o3.binary}, 1,
                                       s["confirm_reps"], timeout)
                sp = speedup_ci(samp["O3"], samp["best"], t["bootstrap_samples"], seed)
                key = {"benchmark": bench.name, "mode": args.mode, "seed": seed}
                timing_f.write(json.dumps({**key, "salt": None, **samp}) + "\n")
                row.update(speedup_vs_O3=sp.ratio, ci_lo=sp.ci_lo, ci_hi=sp.ci_hi,
                           best_screen_s=best.screen_best, best_len=len(names),
                           best_compile_s=best.compile_s, best_text_size=best.text_size,
                           best_pipeline=" ".join(names))
                if sp.ci_lo > 1:
                    ratios = []
                    for salt in s["layout_salts"]:
                        ls = run_interleaved({"best": best.binary, "O3": o3.binary}, 0,
                                             s["layout_reps"], timeout,
                                             env={"LLVM04_LAYOUT_SALT": str(salt)})
                        ratios.append(float(np.median(ls["O3"]) / np.median(ls["best"])))
                        timing_f.write(json.dumps({**key, "salt": salt, **ls}) + "\n")
                    row["layout_min_ratio"] = min(ratios)
                    row["verdict"] = "WIN" if min(ratios) > 1 else "LAYOUT_ONLY"
                elif sp.ci_hi < 1:
                    row["verdict"] = "SLOWER"
                else:
                    row["verdict"] = "TIE"
            writer.writerow(row)
            csv_f.flush()
            sp_txt = (f"{row['speedup_vs_O3']:.3f}x [{row['ci_lo']:.3f}, {row['ci_hi']:.3f}]"
                      if "speedup_vs_O3" in row else "-")
            print(f"{bench.name:<15} seed {seed}  {row['verdict']:<12} {sp_txt}  "
                  f"ok {row['n_ok']} slow {row['n_slow']} miscompile {row['n_miscompile']} "
                  f"crash {row['n_crash']} cerr {row['n_compile_error']} "
                  f"timeout {row['n_timeout']}", flush=True)
    csv_f.close()
    cand_f.close()
    timing_f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
