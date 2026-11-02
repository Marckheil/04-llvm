"""Phase 3: LLM agent episodes, judged exactly like random search.

Per benchmark and seed: start a fresh MCP server (fresh compile budget), let the
agent search and submit, then independently confirm its final build with the same
protocol as random search (o4_llvm/search/confirm.py: 7 interleaved runs against
default<O3>, bootstrap CI, every layout salt). The agent's own measurements never
enter the reported number. If it never submits, its best gated, timed build from the
episode history is confirmed instead (final_source says which).

The model and its prices come from configs/local.yaml (agent.model, agent.price).
Writes results/<out>.csv (one row per episode, appended), and per episode
results/agent_traj/<out>/<bench>.s<seed>.jsonl (transcript) and .calls.jsonl (the
server's call log, for malformed-call rates).
"""

import argparse
import asyncio
import csv
import json
import sys

import anthropic

from o4_llvm.agent.loop import AgentConfig, run_episode
from o4_llvm.agent.mcp_backend import McpBackend
from o4_llvm.bench.machine import preflight_warnings
from o4_llvm.bench.polybench import bitcode_path, build_bitcode, list_benchmarks
from o4_llvm.compiler.toolchain import Toolchain
from o4_llvm.config import load_config, repo_path
from o4_llvm.search.confirm import confirm

FIELDS = ["benchmark", "seed", "verdict", "speedup_vs_O3", "ci_lo", "ci_hi",
          "layout_min_ratio", "final_source", "agent_claimed_speedup", "best_base",
          "best_passes", "budget_used", "stop", "turns", "tool_calls", "tool_errors",
          "malformed", "errors_by_code", "input_tokens", "output_tokens",
          "cache_write_tokens", "cache_read_tokens", "cost_usd", "wall_s", "summary"]


async def episode(cfg: dict, acfg: AgentConfig, client, tc: Toolchain, bench, seed: int,
                  budget: int, traj_dir) -> dict:
    stem = traj_dir / f"{bench.name}.s{seed}"
    with open(f"{stem}.jsonl", "w") as transcript:
        async with McpBackend(budget, log=f"{stem}.calls.jsonl") as be:
            res = await run_episode(client, be, bench.name, budget, acfg, transcript)
            hist = await be.call("get_history", {"benchmark": bench.name})

    cands = {c["build_id"]: c for c in hist.get("candidates", [])}
    final, source = res.submitted, "submitted"
    if final is None:
        timed = [c for c in cands.values() if c.get("gate") == "PASS" and "speedup_vs_O3" in c]
        final = max(timed, key=lambda c: c["speedup_vs_O3"])["build_id"] if timed else None
        source = "history_best" if final else "none"
    row = {"benchmark": bench.name, "seed": seed, "final_source": source,
           "budget_used": hist.get("budget_used"), "stop": res.stop, "turns": res.turns,
           "tool_calls": sum(res.tool_calls.values()), "tool_errors": sum(res.errors.values()),
           "malformed": res.malformed, "errors_by_code": json.dumps(dict(res.errors)),
           "input_tokens": res.usage["input"], "output_tokens": res.usage["output"],
           "cache_write_tokens": res.usage["cache_write"],
           "cache_read_tokens": res.usage["cache_read"], "cost_usd": round(res.cost_usd, 4),
           "wall_s": round(res.wall_s, 1), "summary": res.summary[:400]}
    if final is None:
        row["verdict"] = "NO_CANDIDATE"
        return row
    c = cands[final]
    row.update(best_base=c["base"], best_passes=" ".join(c["passes"]),
               agent_claimed_speedup=c.get("speedup_vs_O3"))
    o3 = tc.build(bitcode_path(cfg, bench, "time"), "default<O3>",
                  repo_path(cfg, "build_dir") / "agent" / bench.name / "O3")
    best_bin = repo_path(cfg, "build_dir") / "mcp" / bench.name / final / "bin"
    result, raw = confirm(cfg, best_bin, o3.binary, seed)
    with open(f"{stem}.confirm.jsonl", "w") as f:
        for r in raw:
            f.write(json.dumps(r) + "\n")
    row.update(result)
    return row


async def main_async(args) -> int:
    cfg = load_config(args.config)
    a = cfg["agent"]
    if args.profile:
        if args.profile not in a.get("profiles", {}):
            print(f"no agent.profiles.{args.profile} in configs/local.yaml", file=sys.stderr)
            return 2
        a = {**a, **a["profiles"][args.profile]}
    if "model" not in a or "price" not in a:
        print("set agent.model and agent.price in configs/local.yaml", file=sys.stderr)
        return 2
    acfg = AgentConfig(model=a["model"], price=a["price"], effort=args.effort or a["effort"],
                       max_tokens=a["max_tokens"], max_turns=args.max_turns or a["max_turns"],
                       max_usd=args.max_usd or a["max_usd"],
                       fallbacks=a.get("fallbacks", True), min_compiles=args.min_compiles)
    budget = args.budget or cfg["episode"]["compile_budget"]
    seeds = args.seeds if args.seeds is not None else cfg["seeds"]
    tc = Toolchain(cfg)
    tc.check_version()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)

    results = repo_path(cfg, "results_dir")
    traj_dir = results / "agent_traj" / args.out
    traj_dir.mkdir(parents=True, exist_ok=True)
    out_csv = results / f"{args.out}.csv"
    new = not out_csv.exists()
    f = open(out_csv, "a", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if new:
        w.writeheader()

    client = anthropic.AsyncAnthropic(max_retries=5)
    spent = 0.0
    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    for bench in benches:
        for variant in ("time", "check"):
            if not bitcode_path(cfg, bench, variant).exists():
                build_bitcode(tc, cfg, bench, variant)
        for seed in seeds:
            if args.total_usd and spent >= args.total_usd:
                print(f"stopping: ${spent:.2f} spent of the ${args.total_usd:.2f} cap")
                f.close()
                return 0
            row = await episode(cfg, acfg, client, tc, bench, seed, budget, traj_dir)
            spent += row["cost_usd"]
            w.writerow(row)
            f.flush()
            sp = (f"{row['speedup_vs_O3']:.3f}x [{row['ci_lo']:.3f}, {row['ci_hi']:.3f}]"
                  if "speedup_vs_O3" in row else "-")
            print(f"{bench.name:<15} seed {seed}  {row['verdict']:<12} {sp}  "
                  f"compiles {row['budget_used']}/{budget}  turns {row['turns']}  "
                  f"stop {row['stop']}  errors {row['tool_errors']} "
                  f"(malformed {row['malformed']})  ${row['cost_usd']:.2f}  "
                  f"{row['wall_s']:.0f}s  | {row.get('best_base', '')} "
                  f"{row.get('best_passes', '')}", flush=True)
    f.close()
    print(f"total ${spent:.2f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--seeds", type=int, nargs="*", help="default: config seeds")
    ap.add_argument("--budget", type=int, help="override episode.compile_budget")
    ap.add_argument("--profile", help="an agent.profiles entry in configs/local.yaml")
    ap.add_argument("--effort", help="override agent.effort")
    ap.add_argument("--max-usd", type=float, help="per-episode cap (default agent.max_usd)")
    ap.add_argument("--max-turns", type=int, help="override agent.max_turns")
    ap.add_argument("--min-compiles", type=int, default=0,
                    help="refuse submit until this many compiles are used (D15)")
    ap.add_argument("--total-usd", type=float, help="stop starting episodes past this spend")
    ap.add_argument("--out", default="agent")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
