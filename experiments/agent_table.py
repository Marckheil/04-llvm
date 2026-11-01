"""Phase 3 results table: agent arms next to both random-search arms.

Reads results/random_search_<mode>.csv and, per agent arm, results/<stem>.csv from
experiments/agent_episodes.py. Arms are given as label=stem so that the model behind
an arm stays in configs/local.yaml. Writes results/summary/agent_table.{md,csv}.

Besides the per-benchmark verdicts it reports per arm: spend, cost per material win,
compile budget used, malformed-call rate, and the A/A false-alarm rate. Every agent
episode that submitted plain default<O3> is an A/A test of the confirmation protocol:
the submission has the same machine code as the reference, so any verdict other than
TIE is a false alarm. "Material" wins are WINs at or above MATERIAL, the largest
effect seen in those A/A runs rounded up.
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from statistics import mean, median

from llvm04.config import load_config, repo_path

MODES = ["o3_suffix", "scratch"]
MATERIAL = 1.03


def load(path):
    rows = defaultdict(list)
    for r in csv.DictReader(open(path)):
        rows[r["benchmark"]].append(r)
    return rows


def sp(r):
    return float(r["speedup_vs_O3"]) if r.get("speedup_vs_O3") else None


def cell(rs):
    if not rs:
        return "-", ""
    sps = [s for s in map(sp, rs) if s is not None]
    v = Counter(r["verdict"] for r in rs)
    return (f"{median(sps):.3f}" if sps else "-"), " ".join(f"{k}:{n}" for k, n in sorted(v.items()))


def is_plain_o3(r):
    return r.get("best_base") == "O3" and not r.get("best_passes")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", action="append", required=True, metavar="LABEL=STEM",
                    help="an agent arm: label for the table, stem of results/<stem>.csv")
    args = ap.parse_args()
    cfg = load_config()
    res = repo_path(cfg, "results_dir")
    arms = {m: load(res / f"random_search_{m}.csv") for m in MODES}
    agents = {}
    for a in args.arm:
        label, stem = a.split("=", 1)
        agents[label] = load(res / f"{stem}.csv")
    arms.update(agents)

    benches = sorted(set().union(*arms.values()))
    head = ["benchmark"] + [f"{a}_{k}" for a in arms for k in ("median_speedup", "verdicts")]
    table = []
    for b in benches:
        row = {"benchmark": b}
        for a, rows in arms.items():
            row[f"{a}_median_speedup"], row[f"{a}_verdicts"] = cell(rows.get(b, []))
        table.append(row)

    out = res / "summary"
    with open(out / "agent_table.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=head)
        w.writeheader()
        w.writerows(table)

    lines = ["# Phase 3 results", "",
             "Median confirmed speedup vs `default<O3>` over seeds, then verdict counts, per arm. "
             "Random-search arms are from Phase 1 (D9); agent arms from D13. All arms: 30-compile "
             "budget, the same confirmation protocol (llvm04/search/confirm.py).", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(r[h] for h in head) + " |" for r in table]

    lines += ["", "## Agent arms", "",
              "| arm | episodes | benchmarks | spend | WIN | material WIN (>= "
              f"{MATERIAL}x) | $ per material WIN | compiles used (mean) | malformed calls "
              "| plain-O3 submissions | A/A false alarms | largest A/A effect |",
              "|" + "---|" * 12]
    for a, rows in agents.items():
        rs = [r for v in rows.values() for r in v]
        cost = sum(float(r["cost_usd"]) for r in rs)
        wins = [r for r in rs if r["verdict"] == "WIN"]
        material = [r for r in wins if sp(r) >= MATERIAL]
        calls = sum(int(r["tool_calls"]) for r in rs)
        malformed = sum(int(r["malformed"]) for r in rs)
        aa = [r for r in rs if is_plain_o3(r) and sp(r) is not None]
        fa = [r for r in aa if r["verdict"] != "TIE"]
        big = max((abs(sp(r) - 1) for r in aa), default=0)
        lines.append(
            f"| {a} | {len(rs)} | {len(rows)} | ${cost:.2f} | {len(wins)} | {len(material)} | "
            + (f"${cost / len(material):.2f}" if material else "-")
            + f" | {mean(int(r['budget_used']) for r in rs):.1f} / 30 | {malformed} / {calls} "
            f"({100 * malformed / calls:.1f}%) | {len(aa)} | {len(fa)} "
            f"({', '.join(r['benchmark'] + ' ' + r['verdict'] for r in fa) or 'none'}) "
            f"| {100 * big:.1f}% |")
        for r in material:
            lines.append(f"  - {a} material WIN: {r['benchmark']} seed {r['seed']} "
                         f"{sp(r):.3f}x [{float(r['ci_lo']):.3f}, {float(r['ci_hi']):.3f}], "
                         f"worst layout {float(r['layout_min_ratio']):.3f}x: "
                         f"`{r['best_base']}` + `{r['best_passes']}`")
        stops = Counter(r["stop"] for r in rs)
        src = Counter(r["final_source"] for r in rs)
        lines.append(f"  - stops {dict(stops)}; final build source {dict(src)}")
    (out / "agent_table.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
