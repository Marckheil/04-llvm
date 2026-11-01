"""Which passes of an agent-found winner carry the win? (docs/decisions.md D14)

Greedy one-pass-at-a-time removal, last pass first: a removal is kept when the
pipeline still passes the correctness gate (D8) and keeps at least --keep of the
full pipeline's gain over default<O3> (gain = speedup - 1, the full pipeline timed with
--confirm-reps reps) in an interleaved --reps screen. A removal that no longer compiles
fails.
The minimal pipeline is then re-timed with --confirm-reps reps and a bootstrap CI.
The gains measured here are 15-25%, far above the 1-2% run-to-run noise, so a
5-rep screen separates "still wins" from "lost it".
Writes results/<out>.csv (one row per step).
"""

import argparse
import csv
import sys

import numpy as np

from llvm04.bench import correctness
from llvm04.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from llvm04.bench.timing import run_interleaved, speedup_ci
from llvm04.compiler.pipeline import load_passes, render
from llvm04.compiler.toolchain import CompileError, Toolchain
from llvm04.config import load_config, repo_path
from llvm04.tools.service import BASES

FIELDS = ["benchmark", "step", "removed", "kept", "gate", "speedup_vs_O3", "ci_lo", "ci_hi",
          "passes"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", required=True)
    ap.add_argument("--base", default="none", choices=list(BASES))
    ap.add_argument("--passes", required=True, help="space-separated pass names")
    ap.add_argument("--keep", type=float, default=0.9)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--confirm-reps", type=int, default=11)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cfg = load_config(args.config)
    tc = Toolchain(cfg)
    tc.check_version()
    timeout, nboot, seed = cfg["limits"]["run_timeout_s"], cfg["timing"]["bootstrap_samples"], cfg["seeds"][0]
    known = load_passes()
    bench = next(b for b in list_benchmarks(cfg) if b.name == args.bench)
    for variant in ("time", "check"):
        if not bitcode_path(cfg, bench, variant).exists():
            build_bitcode(tc, cfg, bench, variant)
    work = repo_path(cfg, "build_dir") / "ablation" / bench.name
    o3 = tc.build(bitcode_path(cfg, bench, "time"), "default<O3>", work / "O3").binary
    builds = {}

    def build(passes):
        key = " ".join(passes)
        if key not in builds:
            pipe = ",".join(p for p in (BASES[args.base], render(passes, known)) if p)
            d = work / f"p{len(builds)}"
            b = tc.build(bitcode_path(cfg, bench, "time"), pipe, d)
            chk = tc.build(bitcode_path(cfg, bench, "check"), pipe, d.with_name(d.name + ".check"))
            builds[key] = (b.binary, correctness.check(tc, cfg, bench, chk.binary))
        return builds[key]

    def screen(passes, reps):
        try:
            binary, gate = build(passes)
        except CompileError:  # e.g. instcombine's fixpoint check (D9); the removal fails
            return "COMPILE_ERROR", None
        if not gate.ok:
            return gate.status, None
        s = run_interleaved({"O3": o3, "cand": binary}, 1, reps, timeout)
        return gate.status, speedup_ci(s["O3"], s["cand"], nboot, seed)

    rows = []

    def record(step, removed, kept, gate, sp, passes):
        rows.append({"benchmark": bench.name, "step": step, "removed": removed, "kept": kept,
                     "gate": gate, "passes": " ".join(passes),
                     **({"speedup_vs_O3": sp.ratio, "ci_lo": sp.ci_lo, "ci_hi": sp.ci_hi}
                        if sp else {})})
        print(f"{step:>3} {('-' + removed) if removed else 'full':<32} gate {gate:<10} "
              + (f"{sp.ratio:.3f}x" if sp else "-") + ("  kept" if kept else ""), flush=True)

    cur = args.passes.split()
    gate, sp = screen(cur, args.confirm_reps)  # the reference gain: one noisy screen is not enough
    if sp is None or sp.ratio <= 1:
        print(f"full pipeline does not win ({gate}); nothing to ablate")
        return 1
    full_gain = sp.ratio - 1
    record(0, "", True, gate, sp, cur)
    step = 0
    for i in reversed(range(len(cur))):
        step += 1
        trial = cur[:i] + cur[i + 1:]
        if not trial and not BASES[args.base]:
            continue
        gate, sp = screen(trial, args.reps)
        kept = sp is not None and sp.ratio - 1 >= args.keep * full_gain
        record(step, cur[i], kept, gate, sp, trial)
        if kept:
            cur = trial

    gate, sp = screen(cur, args.confirm_reps)
    record(step + 1, "", True, f"{gate}/final", sp, cur)
    out = repo_path(cfg, "results_dir") / f"{args.out}.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"minimal: {args.base} + {' '.join(cur)}  ->  {sp.ratio:.3f}x "
          f"[{sp.ci_lo:.3f}, {sp.ci_hi:.3f}]\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
