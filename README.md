# 04llvm

An LLM agent that tunes LLVM optimization pipelines, and a measurement setup strict enough to tell whether its wins are real.

The agent gets a budget of 30 compiles per benchmark. It works through MCP tools: compile a pipeline, check the output is correct, time it against `-O3`, read optimization remarks. At the end it submits its best build. An independent protocol then re-times that build before anything is reported. The agent is compared against random search with the same budget, on 22 PolyBench kernels on an Apple M2.

## Results

| Benchmark | Speedup over `-O3` | Found by | Why it's faster |
|---|---|---|---|
| jacobi-2d | **1.24×** | random search and both agents, every seed | Address arithmetic changes so loads pair into `ldp` instructions: 29% fewer load/store instructions |
| floyd-warshall | **1.17×** | both agents (1 in 2–3 seeds), never random search | O3's vectorized loop never runs: its runtime alias check always fails. Skipping vectorization lets the scalar loop be unrolled instead |
| syr2k | 1.19× | random search only | Not an optimization: one extra store happens to relieve a dispatch bottleneck (D10, D12) |

On the other 19 benchmarks, no method beats `-O3` by 3% or more.

With the full 30-compile budget, the small model found both real wins at **$0.25 per win**; the large model cost $1.58 per win. Total API spend for the project was under $28. Full numbers are in [results/summary/agent_table_d15.md](results/summary/agent_table_d15.md).

## Why the numbers can be trusted

- **Correctness gate.** Every candidate's output arrays are compared with `-O3`'s at full precision before it is timed. Across about 4,000 random candidates, none was a miscompile.
- **Layout control.** Array placement is pinned by a small runtime and re-salted. One benchmark ran 20% slower purely because of where macOS's `malloc` put two arrays (D7). A win must hold under every layout.
- **Noise calibration.** 74 agent submissions were plain `-O3`, machine code identical to the reference. Re-timing them gave 4 false alarms, all under 2.3%. Hence the 3% bar.
- **Mechanisms, not just numbers.** Each win is explained with pass ablation, the generated assembly, and CPU hardware counters (D10, D12, D14).

The full reasoning, including what went wrong, is in [docs/decisions.md](docs/decisions.md) (D1–D15).

## Layout

```
llvm04/compiler   opt/llc driver, pipeline rendering (configs/passes.yaml)
llvm04/bench      PolyBench builds, timing, correctness gate
llvm04/search     random search and the confirmation protocol
llvm04/tools      MCP server: the agent's tool layer (budgeted, gated, logged)
llvm04/agent      the agent loop and prompt
experiments/      one script per experiment; outputs go to results/
results/summary/  committed result tables and logs
```

## Setup

Apple Silicon Mac, macOS, LLVM 22.1.8.

```sh
brew install llvm@22
xcode-select --install              # macOS SDK, if not already installed
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
benchmarks/fetch.sh                 # PolyBench/C 4.2.1, checksum-verified
python benchmarks/build_bitcode.py
pytest
```

Timing runs need the Mac plugged in, Low Power Mode off, and other apps idle.

## Running

```sh
# Random search: 22 benchmarks x 3 seeds, about 4 hours
python experiments/random_search.py --mode scratch --seeds 0 1 2 --out random_search_scratch

# Agent episodes (needs an API key in the environment, and configs/local.yaml
# with agent.model and agent.price; that file is not tracked)
python experiments/agent_episodes.py --bench jacobi-2d --seeds 0 --min-compiles 30 --max-usd 0.5 --out agent

# Results table
python experiments/agent_table.py --arm agent=agent
```
