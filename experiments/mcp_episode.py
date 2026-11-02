"""Drive one tuning episode by hand through the MCP server (Phase 2 "done when").

Starts `python -m o4_llvm.tools.server` as a subprocess and talks to it over stdio
with the official MCP client, exactly as an agent would. The script plays the
agent on jacobi-2d: look at the benchmark, read O3's missed-optimization remarks,
make a typo (rejected for free), compile the fix found in Phase 1, gate it, time
it with the layout check, and read back the history.
Writes results/<out>.jsonl (every request and full response) and prints a summary.
"""

import argparse
import asyncio
import json
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from o4_llvm.config import REPO_ROOT, load_config, repo_path

BENCH = "jacobi-2d"


def payload(res) -> dict:
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def brief(d, width=150) -> str:
    s = json.dumps(d)
    return s if len(s) <= width else s[:width] + " ..."


async def episode(log) -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "o4_llvm.tools.server"],
                                   cwd=str(REPO_ROOT))
    async with stdio_client(params) as streams, ClientSession(*streams) as s:
        init = await s.initialize()
        tools = (await s.list_tools()).tools
        print(f"server {init.server_info.name}: {len(tools)} tools: "
              + ", ".join(t.name for t in tools))
        log.write(json.dumps({"tools": [t.model_dump(mode="json") for t in tools]}) + "\n")

        async def call(name, **args):
            t0 = time.perf_counter()
            out = payload(await s.call_tool(name, args))
            dt = time.perf_counter() - t0
            log.write(json.dumps({"tool": name, "args": args, "latency_s": dt, "result": out})
                      + "\n")
            tag = "ok" if out.get("ok") else f"ERROR {out['error']['code']}"
            print(f"\n> {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})  "
                  f"[{tag}, {dt:.2f}s]")
            return out

        r = await call("list_benchmarks", category="stencils")
        print("  ", [(b["name"], b["O3_median_s"]) for b in r["benchmarks"]])
        r = await call("get_baseline", benchmark=BENCH, level="O3")
        print("  ", brief(r))
        r = await call("get_ir_features", benchmark=BENCH)
        print("   kernel:", brief(r["by_function"]["kernel_jacobi_2d"]))

        o3 = await call("compile", benchmark=BENCH, passes=[], base="O3")
        print("  ", brief(o3))
        # At O3 the kernel is inlined into main, so that is where its remarks land.
        r = await call("get_remarks", build_id=o3["build_id"], kind="missed",
                       function="main", limit=5)
        print(f"   totals {r['totals_by_kind']}")
        for g in r["groups"]:
            print(f"   {g['count']:>3} {g['pass']}/{g['name']}: {g['example'][:110]}")

        r = await call("compile", benchmark=BENCH, passes=["separate-const-offset-from-gp"])
        print("  ", brief(r["error"]))
        b = await call("compile", benchmark=BENCH, passes=["separate-const-offset-from-gep"])
        print("  ", brief(b))
        r = await call("check_correctness", build_id=b["build_id"])
        print("  ", brief(r))
        r = await call("run_benchmark", build_id=b["build_id"], repetitions=7, layout_check=True)
        print("  ", brief(r, 300))
        r = await call("run_benchmark", build_id="deadbeef")
        print("  ", brief(r["error"]))
        r = await call("get_pass_timing", build_id=b["build_id"], top=5)
        print("  ", brief(r, 300))
        h = await call("get_history", benchmark=BENCH)
        print(f"   budget {h['budget_used']}/{h['budget']}; best {brief(h['best'], 300)}")
        return 0 if h["best"] and h["best"]["verdict"] == "FASTER" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="mcp_episode")
    args = ap.parse_args()
    out = repo_path(load_config(), "results_dir") / f"{args.out}.jsonl"
    with open(out, "w") as log:
        rc = asyncio.run(episode(log))
    print(f"\n-> {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
