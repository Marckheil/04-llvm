# Design decisions

One entry per decision: what, why, and what would change it.

## D1. LLVM 22.1.8 via Homebrew `llvm@22` (2026-10-30)
- **What:** All tools come from `/opt/homebrew/opt/llvm@22/bin`, which `configs/default.yaml` pins. `Toolchain.check_version()` fails if the version differs.
- **Why:** The versioned formula never jumps to a new major version, and 22 has had a full release cycle of fixes (23 was the newest stable). Apple's `/usr/bin/clang` is a fork with a different pass set, so it's never used.
- **Revisit if:** we need a pass that only exists in 23+.

## D2. Bitcode built with `-O3 -Xclang -disable-llvm-passes` (2026-10-30)
- **What:** Each benchmark is `polybench.c` + `<bench>.c`, built this way and then `llvm-link`ed into one module. No LLVM passes run.
- **Why:** `-O0` bitcode lacks TBAA and lifetime metadata, so `default<O3>` on it is weaker than real `clang -O3`, which would inflate every win.
- **Why not `-O1`:** we tried it first. At `-O1` the frontend attaches `llvm.loop.unroll.disable` to every loop. For gemm, `default<O3>` then produced 19 vector ops vs 40 for `clang -O3`, and ran ~9% slower. With `-O3` the bitcode differs only in that loop metadata, and the vector op counts match. `experiments/phase0_noise.py` checks runtime parity for every benchmark.

## D3. Fixed backend: `llc -O3` for every candidate (2026-10-30)
- **Why:** Only the middle-end pipeline varies, so differences are attributable to the pipeline. Compile time is `opt + llc` and leaves out linking.

## D4. Timing = PolyBench kernel timer, LARGE dataset, ABBA interleaving (2026-10-30)
- **What:** `-DPOLYBENCH_TIME` (kernel-only wall time), `LARGE_DATASET` by default with per-benchmark overrides, 2 warm-up runs + 15 timed runs in ABBA order, the median, and a bootstrap 95% CI on the ratio of medians.
- **Why:** macOS can't pin cores or disable boost (04LLVM.md 5a). Kernel-only timing excludes init noise.

## D5. Two bitcode variants per benchmark (2026-10-30)
- **What:** `time` (LARGE + timer) and `check` (MEDIUM + `POLYBENCH_DUMP_ARRAYS`). Every candidate pipeline is applied to both.
- **Why:** Array dumps at LARGE are huge and would pollute timing. The correctness gate (Phase 1) diffs the `check` output against `-O0`.
- **Open:** exact match vs a float tolerance; decide in Phase 1.

## D6. Per-benchmark dataset sizes; 8 short kernels excluded (2026-10-30)
- **What:** `experiments/size_datasets.py` picks, for each benchmark, the dataset size where one `default<O3>` run takes 0.5–10 s (log: `results/summary/size_datasets.log`).
  - 18 benchmarks use LARGE. `seidel-2d` is one of them at 17.8 s, because MEDIUM is only 0.12 s.
  - 4 benchmarks use EXTRALARGE: deriche, doitgen, gemm, syrk.
  - 8 benchmarks are excluded: atax, bicg, durbin, gemver, gesummv, jacobi-1d, mvt, trisolv.
- **Why exclude:** these run 3–60 ms even at EXTRALARGE. They are O(N²) kernels or smaller, and memory-bound. At that length the M2's run-to-run noise (6–12% measured on atax) is larger than typical pass-ordering wins (2–5%).
- **Alternatives rejected:**
  - Custom bigger arrays: atax would need ~2 GB, and memory bandwidth would then dominate.
  - Repeating the kernel in a loop: this changes the code being optimized and invites fake wins.
  - A separate "short" tier timed ~200 times: more runs don't remove systematic drift. This could be added in Phase 5 without changing earlier work.
- **Integrity:** the 0.5 s rule and the exclusion list were fixed before any tuning ran. The exclusion is based on `-O3` runtime only, never on how any method performed. Final reported set: 22/30 PolyBench kernels.

## D7. Array placement is pinned by a runtime, not left to macOS malloc (2026-10-30)
- **What:** `benchmarks/runtime/placement.c` is linked into every binary, both pipeline and clang builds, and is never tuned. It overrides `posix_memalign`/`free` for the executable and places every PolyBench array in allocation order inside one 16 MB-aligned arena, with a guard page between arrays. `LLVM04_LAYOUT_SALT=k` moves array i by 3k(i+1) pages. `timing.time_layouts` re-times a binary under several salts.
- **Why:** Phase 0 measured doitgen's `default<O3>` pipeline as 18% slower than clang -O3 (parity 0.820). Root cause:
  - The machine code and layout were identical. Re-signing the binary removed the gap, and a byte-identical relink kept it.
  - Both modes retire the same instructions (32.4 G) at the same clock (3.47 GHz), but the slow one takes 20% more cycles (16.4 G vs 13.7 G).
  - An interposer (`experiments/layout/`) logged allocations over 30 runs of a build that flips modes. The mode followed the placement in all 30 runs: slow exactly when `C4 - sum == 4 MB - 16 KB`. macOS malloc puts `sum` at `...004000` or `...c04000`, and which one it picks depends on the binary's bytes.
  - Forcing that exact distance makes any build slow (4.85 s vs 4.09 s). Moving either array by 16 B or more makes it fast, and so does any other distance tested (2/12/20/36 MB - 16 KB, 4 MB, 4 MB - 32 KB).
  - The kernel stores `sum[p]` on every inner iteration because `sum` may alias `A`/`C4`. With `restrict` on `sum` the store goes away and placement stops mattering (3.55-3.62 s at every distance). This fits a false store-to-load dependence in the M2's hashed memory-dependence prediction. The hash is undocumented, and hardware counters weren't checked.
- **Effect:** doitgen pipeline vs clang is now 4.01-4.14 s vs 3.96-4.10 s, with no slow mode across re-signed copies or salts 0-3. Kernel outputs (`check` variant, all 22 benchmarks) are byte-identical with and without the runtime. doitgen stays in the benchmark set.
- **Integrity:** the layout is the same for every candidate and baseline and is fixed before tuning. Any claimed speedup must still hold under other salts (`time_layouts`), which catches wins that come from layout luck.
- **Note:** removing that store is worth about 12% on doitgen. `loop-versioning-licm` can do it legally, so the tuner can earn that win for real.
- **Sensitivity sweep** (`experiments/layout_sensitivity.py`, salts 0-3, 5 runs each; `results/summary/layout_sensitivity.log`): 19 of 22 benchmarks move at most 2% between layouts.
  - **syr2k:** 12.3%, with tight clusters. Salt 3 runs 0.885 s against 0.994 s at salt 0, and the pinned layout is one of the slow ones.
  - **deriche:** 22.1%. Only salt 2 is affected, and its runs scatter from 0.55 to 0.76 s.
  - **jacobi-2d:** 2.0%, with odd salts 1.8% faster.
  - **Consequence:** a claimed final speedup must beat the baseline under every salt, comparing the same salt to the same salt. Search itself uses salt 0.
- **Open:** confirm the stall type with hardware counters (needs Xcode Instruments or kperf with sudo). Recheck deriche at salt 2.

## D8. Correctness gate: full-precision array compare with a 1e-9 scaled tolerance (2026-10-30)
- **What:** `llvm04/bench/correctness.py`. Both the candidate and a clang `-O0` reference are built from the `check` variant and run with `LLVM04_DUMP_DIR`, so `benchmarks/runtime/placement.c` writes every array's final bytes at exit. Each array is compared:
  - bit-identical: pass
  - integer data, or a size or count change: must be exact, else `MISCOMPILE`
  - float data: NaN positions must match, and `max|cand - ref| <= rtol * max|ref|` with `rtol = 1e-9`
  - a crash returns `CRASH` and a timeout returns `TIMEOUT`
- **Why not PolyBench's array dump:** it prints 2 decimals. A corrupted ludcmp (one constant scaled by 1 + 1e-7, giving 4.49250004925 instead of 4.4925) prints identically, but the gate flags it.
- **Why not bit-exact:** 21/22 benchmarks are bit-identical to `-O0` at O1/O2/O3/Os. ludcmp is not: clang emits `llvm.fmuladd`, which LLVM may legally fuse or not, and its triangular solves differ by 3.3e-16 (scaled) at O2/O3/Os. Without fast-math, passes can't reassociate, so legal differences should stay near this size. 1e-9 leaves 6 orders of headroom.
- **Validation** (`experiments/gate_validation.py`, `results/summary/gate_validation.log`): corrupted O3 builds of gemm, ludcmp and floyd-warshall (`fmul`->`fadd`, or `add`->`sub` for int; a constant x(1+1e-7)) are all `MISCOMPILE`. The unmodified builds pass.
- **Limit:** the subtle gemm mutation produced a scaled error of only 1.05e-9, so corruptions around 1e-9 relative or smaller are at the gate's floor. Every non-bitwise pass is logged with its error so the threshold can be revisited.
- **Not yet:** sanitizer runs (ASan/UBSan) as an optional stricter check.

## D9. Action space and random-search protocol (2026-10-30)
- **Action space:** a curated list of 70 passes in `configs/passes.yaml`, grouped by level: module, cgscc, function, and loop (rendered as `function(loop-mssa(...))`). A pipeline is a sequence of names, and `compiler/pipeline.py` renders it, so every sampled pipeline is well-formed by construction. Unknown names raise `InvalidPipeline` with close-match suggestions, which the Phase 2 tools will return.
  - Started from 78 candidates, all valid in LLVM 22.1.8.
  - Dropped 8 that do nothing on PolyBench or duplicate the backend: annotation-remarks, always-inline, strip-dead-prototypes, lower-expect, alignment-from-assumptions, called-value-propagation, loop-predication, and loop-reduce (llc already runs LSR).
- **Random search, two arms** (`search.modes`), each with the same budget:
  - `scratch`: 5-40 random passes. In a smoke test, all 3 trmm candidates were more than 2x slower than O3, so this arm alone is a weak bar.
  - `o3_suffix`: `default<O3>` plus 1-10 random passes. This is the neighbourhood the agent and ranker will mostly search, so it's the bar that matters.
  - Passes are drawn uniformly with replacement, seeded by `<benchmark>/<mode>/<seed>`.
  - Budget: `episode.compile_budget` = 30 candidates per benchmark and seed. Every attempt costs one, including failures.
  - Seeds: 0, 1, 2.
- **Screening and confirmation:**
  - Each candidate is built in both variants and gated (D8), then timed once. Runs slower than 2x the O3 median are killed and logged as `slow`.
  - The fastest survivor is re-timed against `default<O3>` (7 interleaved runs, bootstrap CI).
  - If the CI excludes 1 from above, both are timed again under layout salts 0-3 (D7). The verdict is `WIN` only if the candidate is faster under every salt, otherwise `LAYOUT_ONLY`. A CI below 1 is `SLOWER`; otherwise `TIE`.
- **Why one screening run:** the budget and the 22 benchmarks make repeated screening runs cost hours. Ranking noise only affects which candidate gets confirmed, never the reported number, which always comes from the confirmation runs.
