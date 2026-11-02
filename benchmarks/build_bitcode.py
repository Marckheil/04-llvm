"""Build PolyBench bitcode (time + check variants) for every benchmark."""

import argparse
import sys

from o4_llvm.bench.polybench import VARIANTS, build_bitcode, list_benchmarks
from o4_llvm.compiler.toolchain import CompileError, Toolchain
from o4_llvm.config import load_config


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config")
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    args = ap.parse_args()

    cfg = load_config(args.config)
    tc = Toolchain(cfg)
    tc.check_version()

    benches = [b for b in list_benchmarks(cfg) if not args.bench or b.name in args.bench]
    failed = []
    for bench in benches:
        for variant in VARIANTS:
            try:
                out = build_bitcode(tc, cfg, bench, variant)
                print(f"ok   {bench.name:<16} {variant:<5} {out}")
            except CompileError as e:
                failed.append((bench.name, variant))
                print(f"FAIL {bench.name:<16} {variant:<5} {e}", file=sys.stderr)

    print(f"\n{len(benches) * len(VARIANTS) - len(failed)} built, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
