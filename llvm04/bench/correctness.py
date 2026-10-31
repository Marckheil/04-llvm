"""Correctness gate: a candidate's final arrays must match the clang -O0 reference (D8).

Both builds use the `check` variant and run with LLVM04_DUMP_DIR set, so the
placement runtime writes every PolyBench array's final bytes at exit. Blocks are
compared at full precision:
  - bit-identical                    -> pass
  - integer data, or any byte change
    in a non-float block             -> MISCOMPILE
  - float data                       -> pass iff NaNs line up and
                                        max|cand - ref| <= rtol * max|ref| per block
The tolerance exists because clang marks `a*b + c` as llvm.fmuladd, which LLVM may
legally fuse or not; passes that change that choice move the last few bits.
"""

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from llvm04 import proc
from llvm04.bench.polybench import Benchmark, build_from_source
from llvm04.compiler.toolchain import Toolchain
from llvm04.config import repo_path

PASS, MISCOMPILE, CRASH, TIMEOUT = "PASS", "MISCOMPILE", "CRASH", "TIMEOUT"


@dataclass
class GateResult:
    status: str
    detail: str = ""
    bitwise: bool = False
    max_rel_err: float = 0.0  # worst scaled error over float blocks

    @property
    def ok(self) -> bool:
        return self.status == PASS


def element_dtype(bench: Benchmark) -> np.dtype:
    """PolyBench's default DATA_TYPE for this benchmark, read from its header."""
    header = bench.source.with_suffix(".h").read_text()
    m = re.search(r"#\s*define\s+DATA_TYPE_IS_(INT|FLOAT|DOUBLE)", header)
    return np.dtype({"INT": np.int32, "FLOAT": np.float32, "DOUBLE": np.float64}[m[1] if m else "DOUBLE"])


def _run_dump(binary: Path, out_dir: Path, timeout_s: float) -> proc.ProcResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    return proc.run([binary], timeout_s, env={"LLVM04_DUMP_DIR": str(out_dir)})


def _blocks(d: Path) -> list[bytes]:
    files = sorted(d.glob("block*.bin"), key=lambda p: int(p.stem[5:]))
    return [f.read_bytes() for f in files]


def reference_dir(tc: Toolchain, cfg: dict, bench: Benchmark) -> Path:
    """Dumped arrays of the clang -O0 check build; built once and cached."""
    d = repo_path(cfg, "build_dir") / "reference" / f"{bench.name}.{cfg['polybench']['check_dataset']}"
    if (d / "done").exists():
        return d
    shutil.rmtree(d, ignore_errors=True)
    binary = build_from_source(tc, cfg, bench, "check", "-O0", d / f"{bench.name}.O0")
    res = _run_dump(binary, d / "blocks", cfg["limits"]["run_timeout_s"])
    if res.returncode != 0:
        raise RuntimeError(f"-O0 reference for {bench.name} failed: {res.stderr[-300:]}")
    (d / "done").touch()
    return d


def compare(ref: list[bytes], cand: list[bytes], dtype: np.dtype, rtol: float) -> GateResult:
    if len(ref) != len(cand):
        return GateResult(MISCOMPILE, f"{len(cand)} arrays, expected {len(ref)}")
    if ref == cand:
        return GateResult(PASS, bitwise=True)
    worst = 0.0
    for i, (r, c) in enumerate(zip(ref, cand)):
        if r == c:
            continue
        if len(r) != len(c):
            return GateResult(MISCOMPILE, f"array {i}: {len(c)} bytes, expected {len(r)}")
        if dtype.kind != "f" or len(r) % dtype.itemsize:
            return GateResult(MISCOMPILE, f"array {i}: non-float data differs")
        a, b = np.frombuffer(r, dtype), np.frombuffer(c, dtype)
        if not np.array_equal(np.isnan(a), np.isnan(b)):
            return GateResult(MISCOMPILE, f"array {i}: NaN positions differ")
        ok = ~np.isnan(a)
        scale = float(np.max(np.abs(a[ok]))) if ok.any() else 0.0
        err = float(np.max(np.abs(a[ok] - b[ok]))) if ok.any() else 0.0
        rel = err / scale if scale else (0.0 if err == 0 else np.inf)
        worst = max(worst, rel)
        if rel > rtol:
            idx = int(np.argmax(np.abs(np.where(ok, a - b, 0))))
            return GateResult(MISCOMPILE, f"array {i}: scaled error {rel:.3g} > {rtol:g} "
                              f"(element {idx}: {b[idx]!r} vs {a[idx]!r})", max_rel_err=rel)
    return GateResult(PASS, f"within tolerance (scaled error {worst:.3g})", max_rel_err=worst)


def check(tc: Toolchain, cfg: dict, bench: Benchmark, binary: Path) -> GateResult:
    """Run a candidate built from the `check` bitcode and compare it with the reference."""
    ref = reference_dir(tc, cfg, bench)
    with tempfile.TemporaryDirectory(prefix="gate-") as tmp:
        try:
            res = _run_dump(binary, Path(tmp), cfg["limits"]["run_timeout_s"])
        except proc.ProcTimeout:
            return GateResult(TIMEOUT, f"no exit within {cfg['limits']['run_timeout_s']}s")
        if res.returncode != 0:
            return GateResult(CRASH, f"exit {res.returncode}: {res.stderr.strip()[-300:]}")
        return compare(_blocks(ref / "blocks"), _blocks(Path(tmp)), element_dtype(bench),
                       cfg["correctness"]["rtol"])
