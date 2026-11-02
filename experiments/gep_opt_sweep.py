"""Does separate-const-offset-from-gep help beyond jacobi-2d? (docs/decisions.md D10)

Random search found jacobi-2d 1.24x faster than default<O3> on every seed, and every
o3_suffix winner contained separate-const-offset-from-gep. For every benchmark this
times three builds, all gated (D8), interleaved against default<O3>:
  O3         default<O3>
  O3+scofg   default<O3>,function(separate-const-offset-from-gep)
  O3+gepopt  default<O3> with llc -aarch64-enable-gep-opt (the backend's own,
             off-by-default switch that runs the same pass before codegen)
Winners are then re-timed under every layout salt (D7).
Writes results/<out>.csv and results/<out>_samples.json.
"""

import argparse
import copy
import csv
import json
import sys

import numpy as np

from o4_llvm.bench import correctness
from o4_llvm.bench.machine import preflight_warnings
from o4_llvm.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from o4_llvm.bench.timing import run_interleaved, speedup_ci
from o4_llvm.compiler.pipeline import load_passes, render
from o4_llvm.compiler.toolchain import Toolchain
from o4_llvm.config import load_config, repo_path

FIELDS = ["benchmark", "variant", "gate", "median_s", "speedup_vs_O3", "ci_lo", "ci_hi",
          "layout_min_ratio", "text_size"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--out", default="gep_opt_sweep")
    args = ap.parse_args()

    cfg = load_config(args.config)
    s, t = cfg["search"], cfg["timing"]
    timeout = cfg["limits"]["run_timeout_s"]
    gep_cfg = copy.deepcopy(cfg)
    gep_cfg["target"]["llc_flags"] = ["-aarch64-enable-gep-opt"]
    tc, tc_gep = Toolchain(cfg), Toolchain(gep_cfg)
    tc.check_version()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)
    scofg = "default<O3>," + render(["separate-const-offset-from-gep"], load_passes())
    variants = {"O3": (tc, "default<O3>"), "O3+scofg": (tc, scofg),
                "O3+gepopt": (tc_gep, "default<O3>")}

    results_dir = repo_path(cfg, "results_dir")
    results_dir.mkdir(parents=True, exist_ok=True)
    rows, raw = [], {}
    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    for bench in benches:
        for variant in ("time", "check"):
            if not bitcode_path(cfg, bench, variant).exists():
                build_bitcode(tc, cfg, bench, variant)
        work = repo_path(cfg, "build_dir") / "gep_opt" / bench.name
        bins, gates, sizes = {}, {}, {}
        for name, (vtc, pipeline) in variants.items():
            b = vtc.build(bitcode_path(cfg, bench, "time"), pipeline, work / name)
            chk = vtc.build(bitcode_path(cfg, bench, "check"), pipeline, work / f"{name}.check")
            gates[name] = correctness.check(vtc, cfg, bench, chk.binary)
            sizes[name] = b.text_size
            if gates[name].ok:
                bins[name] = b.binary
            else:
                print(f"{bench.name} {name}: gate {gates[name].status}: {gates[name].detail}")

        samp = run_interleaved(bins, 1, args.reps, timeout)
        raw[bench.name] = {"main": samp}
        line = [f"{bench.name:<15}"]
        for name in variants:
            row = {"benchmark": bench.name, "variant": name, "gate": gates[name].status,
                   "text_size": sizes[name]}
            if name in samp:
                sp = speedup_ci(samp["O3"], samp[name], t["bootstrap_samples"], cfg["seeds"][0])
                row.update(median_s=float(np.median(samp[name])), speedup_vs_O3=sp.ratio,
                           ci_lo=sp.ci_lo, ci_hi=sp.ci_hi)
                if name != "O3" and sp.ci_lo > 1:
                    ratios = []
                    for salt in s["layout_salts"]:
                        ls = run_interleaved({"O3": bins["O3"], name: bins[name]}, 0,
                                             s["layout_reps"], timeout,
                                             env={"LLVM04_LAYOUT_SALT": str(salt)})
                        raw[bench.name][f"{name}/salt{salt}"] = ls
                        ratios.append(float(np.median(ls["O3"]) / np.median(ls[name])))
                    row["layout_min_ratio"] = min(ratios)
                line.append(f"{name} {sp.ratio:5.3f}x [{sp.ci_lo:.3f}, {sp.ci_hi:.3f}]")
            else:
                line.append(f"{name} {gates[name].status}")
            rows.append(row)
        print("  ".join(line), flush=True)

    out = results_dir / f"{args.out}.csv"
    (results_dir / f"{args.out}_samples.json").write_text(json.dumps(raw, indent=1))
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"\nGeomean speedup vs default<O3> over {len(benches)} benchmarks:")
    for name in variants:
        sp = [r["speedup_vs_O3"] for r in rows if r["variant"] == name and "speedup_vs_O3" in r]
        n_win = sum(1 for r in rows if r["variant"] == name and r.get("layout_min_ratio", 0) > 1)
        n_loss = sum(1 for r in rows if r["variant"] == name and r.get("ci_hi", 2) < 1)
        print(f"  {name:<10} {float(np.exp(np.mean(np.log(sp)))):.3f}x  "
              f"faster under every salt: {n_win}  slower (CI < 1): {n_loss}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
