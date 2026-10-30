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
