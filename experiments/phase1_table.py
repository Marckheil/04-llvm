"""Phase 1 results table: default<O3>, the other levels, and both random-search arms.

Reads results/baselines.csv and results/random_search_<mode>.csv (+ _candidates.jsonl)
and writes results/summary/phase1_table.md and results/summary/phase1_table.csv.
Per arm and benchmark it reports the median confirmed speedup over seeds and the
verdicts; a WIN must have held under every layout salt (D7, D9).
"""

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from statistics import median

from o4_llvm.config import load_config, repo_path

MODES = ["o3_suffix", "scratch"]


def geomean(xs):
    xs = [x for x in xs if x]
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float("nan")


def main() -> int:
    cfg = load_config()
    res = repo_path(cfg, "results_dir")
    base = defaultdict(dict)
    for r in csv.DictReader(open(res / "baselines.csv")):
        base[r["benchmark"]][r["level"]] = r

    arms = {}
    for mode in MODES:
        p = res / f"random_search_{mode}.csv"
        if not p.exists():
            continue
        rows = defaultdict(list)
        for r in csv.DictReader(open(p)):
            rows[r["benchmark"]].append(r)
        cands = Counter()
        cand_p = res / f"random_search_{mode}_candidates.jsonl"
        if cand_p.exists():
            for line in open(cand_p):
                c = json.loads(line)
                cands["slow" if c["slow"] else c["status"]] += 1
        arms[mode] = (rows, cands)

    benches = sorted(base)
    head = ["benchmark", "O3_s"] + [f"{l}_vs_O3" for l in ("O1", "O2", "Os")]
    for mode in arms:
        head += [f"{mode}_median_speedup", f"{mode}_verdicts"]
    table = []
    for b in benches:
        row = {"benchmark": b, "O3_s": float(base[b]["O3"]["median_s"])}
        for l in ("O1", "O2", "Os"):
            row[f"{l}_vs_O3"] = float(base[b][l]["speedup_vs_O3"])
        for mode, (rows, _) in arms.items():
            rs = rows.get(b, [])
            sps = [float(r["speedup_vs_O3"]) for r in rs if r["speedup_vs_O3"]]
            row[f"{mode}_median_speedup"] = median(sps) if sps else None
            v = Counter(r["verdict"] for r in rs)
            row[f"{mode}_verdicts"] = " ".join(f"{k}:{n}" for k, n in sorted(v.items()))
        table.append(row)

    out = repo_path(cfg, "results_dir") / "summary"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "phase1_table.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=head)
        w.writeheader()
        w.writerows(table)

    fmt = lambda x: "-" if x is None else f"{x:.3f}"
    lines = ["# Phase 1 results", "",
             "Speedups are vs `default<O3>` (higher is better). Random-search columns: median "
             "confirmed speedup over seeds, then verdict counts. WIN = faster than O3 with a "
             "bootstrap CI above 1 *and* under every layout salt.", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in table:
        cells = [r["benchmark"], f"{r['O3_s']:.3f}"] + [fmt(r[f"{l}_vs_O3"]) for l in ("O1", "O2", "Os")]
        for mode in arms:
            cells += [fmt(r[f"{mode}_median_speedup"]), r[f"{mode}_verdicts"] or "-"]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "**Geomean vs O3:** " + ", ".join(
        f"{l} {geomean([r[f'{l}_vs_O3'] for r in table]):.3f}x" for l in ("O1", "O2", "Os"))
        + "".join(f", {m} {geomean([r[f'{m}_median_speedup'] for r in table]):.3f}x" for m in arms)]
    for mode, (rows, cands) in arms.items():
        v = Counter(r["verdict"] for rs in rows.values() for r in rs)
        lines += [f"", f"**{mode}:** {sum(len(rs) for rs in rows.values())} benchmark x seed runs; "
                  f"verdicts {dict(v)}; candidates {dict(cands)}"]
    (out / "phase1_table.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
