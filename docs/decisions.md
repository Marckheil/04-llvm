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
- **Why:** macOS can't pin cores or disable boost (PASSPILOT.md 5a). Kernel-only timing excludes init noise.

## D5. Two bitcode variants per benchmark (2026-10-30)
- **What:** `time` (LARGE + timer) and `check` (MEDIUM + `POLYBENCH_DUMP_ARRAYS`). Every candidate pipeline is applied to both.
- **Why:** Array dumps at LARGE are huge and would pollute timing. The correctness gate (Phase 1) diffs the `check` output against `-O0`.
- **Open:** exact match vs a float tolerance; decide in Phase 1.
