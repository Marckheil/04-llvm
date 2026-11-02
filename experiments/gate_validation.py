"""Does the correctness gate catch broken builds? (D8, Phase 1 checklist)

Builds default<O3> for a benchmark, then corrupts the optimized IR on purpose:
  gross:  the first `fmul` becomes `fadd` (integer benchmarks: first `add nsw` -> `sub nsw`)
  subtle: the first double constant in fmul/fadd/fsub grows by 1e-7 (relative)
and runs each through the gate (mutations that don't apply are skipped). It also
reports whether PolyBench's own 2-decimal array dump would have noticed, to show
why the gate reads raw arrays.
"""

import argparse
import re
import struct
import sys

from o4_llvm import proc
from o4_llvm.bench import correctness
from o4_llvm.bench.polybench import bitcode_path, list_benchmarks
from o4_llvm.compiler.toolchain import Toolchain
from o4_llvm.config import load_config, repo_path


def nudge_first_double(ir: str) -> str:
    """Scale the first double literal used in an fmul/fadd/fsub by (1 + 1e-7)."""
    pat = re.compile(r"= (?:fmul|fadd|fsub)[^\n]*?(-?\d\.\d+e[+-]\d+|0x[0-9A-F]{16})")
    m = pat.search(ir)
    if not m:
        return ir
    lit = m[1]
    val = struct.unpack(">d", bytes.fromhex(lit[2:]))[0] if lit.startswith("0x") else float(lit)
    new = "0x" + struct.pack(">d", val * (1 + 1e-7)).hex().upper()
    return ir[:m.start(1)] + new + ir[m.end(1):]


def flip_first_op(ir: str) -> str:
    if "= fmul " in ir:
        return ir.replace("= fmul ", "= fadd ", 1)
    return re.sub(r"= add nsw i32 ", "= sub nsw i32 ", ir, count=1)


MUTATIONS = {
    "gross": flip_first_op,
    "subtle": nudge_first_double,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--bench", default="gemm")
    args = ap.parse_args()

    cfg = load_config(args.config)
    tc = Toolchain(cfg)
    bench = next(b for b in list_benchmarks(cfg) if b.name == args.bench)
    work = repo_path(cfg, "build_dir") / "gate_validation"
    work.mkdir(parents=True, exist_ok=True)
    timeout = cfg["limits"]["run_timeout_s"]

    good = tc.build(bitcode_path(cfg, bench, "check"), "default<O3>", work / f"{bench.name}.O3")
    ref_dump = proc.run([good.binary], timeout).stderr
    builds = {"unmodified": good.binary}

    ll = work / f"{bench.name}.O3.ll"
    tc._run("llvm-dis", [tc.tool("llvm-dis"), good.binary.with_name(good.binary.name + ".opt.bc"), "-o", ll])
    for name, mutate in MUTATIONS.items():
        bad_ll = work / f"{bench.name}.{name}.ll"
        text = ll.read_text()
        bad = mutate(text)
        if bad == text:
            print(f"{name:<11} skipped (nothing to mutate)")
            continue
        bad_ll.write_text(bad)
        obj = work / f"{bench.name}.{name}.o"
        tc.llc(bad_ll, obj)
        tc.link([obj], work / f"{bench.name}.{name}")
        builds[name] = work / f"{bench.name}.{name}"

    failures = 0
    for name, binary in builds.items():
        r = correctness.check(tc, cfg, bench, binary)
        dump_differs = proc.run([binary], timeout).stderr != ref_dump
        expected_ok = name == "unmodified"
        verdict = "as expected" if r.ok == expected_ok else "WRONG"
        failures += r.ok != expected_ok
        print(f"{name:<11} gate={r.status:<10} 2-decimal dump differs={dump_differs!s:<5} "
              f"{verdict}  {r.detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
