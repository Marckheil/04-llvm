# PassPilot

Agentic LLVM pass-pipeline autotuner. See [PASSPILOT.md](PASSPILOT.md) for the plan and [docs/decisions.md](docs/decisions.md) for design choices.

**Platform:** Apple M2 (arm64), macOS. **Toolchain:** LLVM 22.1.8 (Homebrew `llvm@22`).

## Setup

```sh
brew install llvm@22
xcode-select --install              # macOS SDK, if not already installed
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
benchmarks/fetch.sh                 # PolyBench/C 4.2.1, checksum-verified
python benchmarks/build_bitcode.py  # build/bitcode/<bench>.{time,check}.bc
```

## Phase 0 check

Plug in, turn off Low Power Mode, and close other apps, then run:

```sh
caffeinate -dimsu python experiments/phase0_noise.py
```

This reports each benchmark's noise (CV), whether its runtime is long enough, and whether our `default<O3>` pipeline matches `clang -O3`. Results go to `results/phase0_noise.csv`.

## Tests

```sh
pytest
```
