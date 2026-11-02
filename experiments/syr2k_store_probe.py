"""Why is random search's syr2k winner 1.17x faster than default<O3>? (docs/decisions.md D10)

The winner's hot loop (the j loop of C[i][j] += A[j][k]*alpha*B[i][k] + ...) has the
same instructions as O3's plus one store: its j counter is never promoted to a
register and is written to the stack every iteration. This probe edits O3's
assembly to isolate that store:
  O3          default<O3>, untouched
  O3+nop      a nop at the top of the hot loop (same code size change, no store)
  O3+store    `str xzr, [sp, #-16]` at the top of the hot loop: a store with no
              data dependence on anything, into the red zone below sp
  win         the scratch seed-0 winner from results/random_search_scratch.csv
  win-store   the winner with its in-loop stack store deleted
Each is timed interleaved against O3, then under every layout salt (D7).
These are timing probes: the edited binaries compute the same values but are not
gated, because the check variant is a different build.
Writes results/<out>.csv and results/<out>_samples.json.
"""

import argparse
import csv
import json
import re
import sys

import numpy as np

from o4_llvm.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from o4_llvm.bench.timing import run_interleaved, speedup_ci
from o4_llvm.compiler.pipeline import load_passes, render
from o4_llvm.compiler.toolchain import Toolchain
from o4_llvm.config import load_config, repo_path


def hot_loop(lines: list[str]) -> tuple[int, int]:
    """(first, last) line index of the depth-3 loop body that contains the fmadd."""
    for i, l in enumerate(lines):
        if "Inner Loop Header: Depth=3" in l:
            j = next(k for k in range(i + 1, len(lines)) if re.match(r"\tb\.\w+\t", lines[k]))
            if any("fmadd" in x for x in lines[i:j]):
                return i, j
    raise ValueError("hot loop not found")


def edit(asm: str, insert: str | None = None, drop_stack_store: bool = False) -> str:
    lines = asm.split("\n")
    first, last = hot_loop(lines)
    body = lines[first + 1:last + 1]
    if drop_stack_store:
        stores = [l for l in body if re.match(r"\tstr\tw\d+, \[sp, #\d+\]", l)]
        if len(stores) != 1:
            raise ValueError(f"expected one stack store in the hot loop, got {stores}")
        body.remove(stores[0])
    if insert:
        body.insert(0, insert)
    return "\n".join(lines[:first + 1] + body + lines[last + 1:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--reps", type=int, default=9)
    ap.add_argument("--out", default="syr2k_store_probe")
    args = ap.parse_args()

    cfg = load_config(args.config)
    s, t = cfg["search"], cfg["timing"]
    timeout = cfg["limits"]["run_timeout_s"]
    tc = Toolchain(cfg)
    tc.check_version()
    bench = next(b for b in list_benchmarks(cfg) if b.name == "syr2k")
    bc = bitcode_path(cfg, bench, "time")
    if not bc.exists():
        build_bitcode(tc, cfg, bench, "time")
    results_dir = repo_path(cfg, "results_dir")
    win = next(r for r in csv.DictReader(open(results_dir / "random_search_scratch.csv"))
               if r["benchmark"] == "syr2k" and r["seed"] == "0")
    pipelines = {"O3": "default<O3>", "win": render(win["best_pipeline"].split(), load_passes())}

    work = repo_path(cfg, "build_dir") / "syr2k_store_probe"
    work.mkdir(parents=True, exist_ok=True)
    asm = {}
    for name, pipeline in pipelines.items():
        opt_bc = work / f"{name}.opt.bc"
        tc.opt(bc, opt_bc, pipeline)
        tc._run("llc", [tc.tool("llc"), tc.codegen_opt, opt_bc, "-o", work / f"{name}.s"])
        asm[name] = (work / f"{name}.s").read_text()
    sources = {
        "O3": asm["O3"],
        "O3+nop": edit(asm["O3"], "\tnop"),
        "O3+store": edit(asm["O3"], "\tstr\txzr, [sp, #-16]"),
        "win": asm["win"],
        "win-store": edit(asm["win"], drop_stack_store=True),
    }
    bins = {}
    for name, src in sources.items():
        path = work / f"{name}.s"
        path.write_text(src)
        tc.clang(["-c", path, "-o", path.with_suffix(".o")])
        tc.link([path.with_suffix(".o")], work / name)
        bins[name] = work / name

    samp = run_interleaved(bins, 1, args.reps, timeout)
    raw, rows = {"main": samp}, []
    for name in bins:
        sp = speedup_ci(samp["O3"], samp[name], t["bootstrap_samples"], cfg["seeds"][0])
        row = {"variant": name, "median_s": float(np.median(samp[name])),
               "speedup_vs_O3": sp.ratio, "ci_lo": sp.ci_lo, "ci_hi": sp.ci_hi}
        rows.append(row)
    for salt in s["layout_salts"]:
        ls = run_interleaved(bins, 0, s["layout_reps"], timeout,
                             env={"LLVM04_LAYOUT_SALT": str(salt)})
        raw[f"salt{salt}"] = ls
        for row in rows:
            row[f"salt{salt}_ratio"] = float(np.median(ls["O3"]) / np.median(ls[row["variant"]]))
    for row in rows:
        salts = [row[f"salt{x}_ratio"] for x in s["layout_salts"]]
        print(f"{row['variant']:<10} {row['median_s']:.4f}s  {row['speedup_vs_O3']:.3f}x "
              f"[{row['ci_lo']:.3f}, {row['ci_hi']:.3f}]  salts "
              + " ".join(f"{x:.3f}" for x in salts), flush=True)

    out = results_dir / f"{args.out}.csv"
    (results_dir / f"{args.out}_samples.json").write_text(json.dumps(raw, indent=1))
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
