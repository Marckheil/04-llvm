"""MCP server over the tool layer (stdio transport, official MCP Python SDK).

    python -m llvm04.tools.server [--config PATH] [--budget N]

Each tool is a thin wrapper over ToolService, which does validation, budgeting,
error typing and call logging. The descriptions here are what the agent reads.
Allowed values are stated in descriptions rather than enforced in the schema, so a
bad argument reaches ToolService and comes back as a typed, logged INVALID_ARGUMENT
instead of a transport-level validation error.
"""

import argparse
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from llvm04.config import load_config
from llvm04.tools.service import ToolService

INSTRUCTIONS = """\
Tune the LLVM middle-end pass pipeline of one PolyBench benchmark to run faster than
default<O3> on this machine (Apple M2, LLVM 22.1.8; the backend is fixed at llc -O3).
An episode is one benchmark with a fixed compile budget. `compile` spends one unit per
distinct pipeline, failures included; repeating a pipeline is free; invalid pass names
are rejected for free. A build must pass `check_correctness` before `run_benchmark` will
time it. Every tool returns {"ok": true, ...} or {"ok": false, "error": {"code", "message",
...}}; codes: INVALID_ARGUMENT, INVALID_PIPELINE, COMPILE_ERROR, MISCOMPILE, CRASH,
TIMEOUT, RESOURCE_LIMIT, BUDGET_EXCEEDED, INTERNAL.
Speedups are only meaningful with their CI; FASTER means the 95% CI is above 1. Ask for
layout_check=true before trusting a win: some wins on this machine come from memory
placement, not from the code.
"""

Bench = Annotated[str, Field(description="Benchmark name from list_benchmarks, e.g. 'gemm'.")]
BuildId = Annotated[str, Field(description="build_id returned by compile.")]


def build_server(svc: ToolService) -> MCPServer:
    mcp = MCPServer("04llvm", instructions=INSTRUCTIONS)

    @mcp.tool()
    def list_benchmarks(
        category: Annotated[str | None, Field(
            description="Substring filter on the category, e.g. 'stencils'.")] = None,
    ) -> dict:
        """List benchmarks with category, timed dataset and default<O3> runtime in seconds."""
        return svc.list_benchmarks(category)

    @mcp.tool()
    def list_passes() -> dict:
        """The action space: every pass name compile accepts, grouped by IR level, plus the
        allowed base pipelines."""
        return svc.list_passes()

    @mcp.tool()
    def get_baseline(
        benchmark: Bench,
        level: Annotated[str, Field(description="One of O0, O1, O2, O3, Os.")] = "O3",
    ) -> dict:
        """Recorded runtime (median, CV, speedup vs O3 with 95% CI), compile time and code
        size of clang's default<level> pipeline for a benchmark."""
        return svc.get_baseline(benchmark, level)

    @mcp.tool()
    def get_ir_features(
        benchmark: Annotated[str | None, Field(
            description="Features of this benchmark's unoptimized input IR.")] = None,
        build_id: Annotated[str | None, Field(
            description="Or: features of this build's optimized IR.")] = None,
    ) -> dict:
        """Instruction-mix, basic-block and loop counts (module total and per loop-carrying
        function). Give exactly one of benchmark or build_id."""
        return svc.get_ir_features(benchmark, build_id)

    @mcp.tool()
    def compile(
        benchmark: Bench,
        passes: Annotated[list[str], Field(
            description="Pass names from list_passes, run in order after `base`. May be "
                        "empty if base is not 'none'.")],
        base: Annotated[str, Field(
            description="Standard pipeline to run first: none, O1, O2, O3, Os or Oz. "
                        "'none' runs only `passes`.")] = "O3",
        rationale: Annotated[str | None, Field(
            description="One or two sentences: why you expect this pipeline to beat what "
                        "you have tried so far. Recorded, not used.")] = None,
    ) -> dict:
        """Compile the benchmark with `base` then `passes`. Returns a build_id, compile time,
        code size and the remaining budget. Costs one unit of budget per distinct pipeline."""
        return svc.compile(benchmark, passes, base, rationale)

    @mcp.tool()
    def check_correctness(build_id: BuildId) -> dict:
        """Run the build on the correctness dataset and compare every output array with the
        clang -O0 reference. Status PASS, MISCOMPILE, CRASH or TIMEOUT."""
        return svc.check_correctness(build_id)

    @mcp.tool()
    def run_benchmark(
        build_id: BuildId,
        repetitions: Annotated[int, Field(description="Timed runs per binary, 1-15.")] = 5,
        layout_check: Annotated[bool, Field(
            description="Also time under 4 memory layouts; a real win holds under all.")] = False,
    ) -> dict:
        """Time the build interleaved with default<O3>. Returns medians, speedup vs O3 with a
        bootstrap 95% CI, and verdict FASTER, TIE or SLOWER. Runs the correctness gate first
        if needed and refuses builds that fail it."""
        return svc.run_benchmark(build_id, repetitions, layout_check)

    @mcp.tool()
    def get_remarks(
        build_id: BuildId,
        kind: Annotated[str, Field(
            description="missed, passed, analysis, failure or all.")] = "missed",
        pass_name: Annotated[str | None, Field(
            description="Only remarks from this pass, e.g. 'loop-vectorize', 'licm'.")] = None,
        function: Annotated[str | None, Field(
            description="Only remarks in this function, e.g. 'kernel_gemm'.")] = None,
        limit: Annotated[int, Field(description="Max groups returned, 1-50.")] = 20,
    ) -> dict:
        """LLVM optimization remarks for the build's pipeline, grouped by (kind, pass, remark,
        function) with counts and one example message each, largest groups first."""
        return svc.get_remarks(build_id, kind, pass_name, function, limit)

    @mcp.tool()
    def get_pass_timing(
        build_id: BuildId,
        top: Annotated[int, Field(description="Max passes returned, 1-50.")] = 20,
    ) -> dict:
        """Wall time per pass for the build's pipeline (opt -time-passes), slowest first."""
        return svc.get_pass_timing(build_id, top)

    @mcp.tool()
    def get_history(benchmark: Bench) -> dict:
        """Every pipeline compiled for this benchmark in this episode, with status, gate and
        timing results, the budget left, and the best gated result so far."""
        return svc.get_history(benchmark)

    return mcp


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--budget", type=int, help="override episode.compile_budget")
    ap.add_argument("--log", help="call log path (default results/mcp_calls.jsonl)")
    args = ap.parse_args()
    svc = ToolService(load_config(args.config), budget=args.budget, log_path=args.log)
    svc.tc.check_version()
    build_server(svc).run()


if __name__ == "__main__":
    main()
