"""PolyBench/C registry and builds (bitcode for tuning, from-source for parity checks)."""

from dataclasses import dataclass
from pathlib import Path

from llvm04.compiler.toolchain import Toolchain
from llvm04.config import repo_path

VARIANTS = ("time", "check")


@dataclass(frozen=True)
class Benchmark:
    name: str
    source: Path

    @property
    def src_dir(self) -> Path:
        return self.source.parent


def list_benchmarks(cfg: dict, include_excluded: bool = False) -> list[Benchmark]:
    root = repo_path(cfg, "polybench_src")
    lines = (root / "utilities" / "benchmark_list").read_text().split()
    benches = [Benchmark(Path(l).stem, (root / l).resolve()) for l in lines]
    if not include_excluded:
        excluded = set(cfg["polybench"].get("exclude", []))
        benches = [b for b in benches if b.name not in excluded]
    return sorted(benches, key=lambda b: b.name)


def defines(cfg: dict, bench: Benchmark, variant: str) -> list[str]:
    pb = cfg["polybench"]
    if variant == "time":
        dataset = pb["dataset_overrides"].get(bench.name, pb["time_dataset"])
        return [f"-D{dataset}", "-DPOLYBENCH_TIME"]
    if variant == "check":
        return [f"-D{pb['check_dataset']}", "-DPOLYBENCH_DUMP_ARRAYS"]
    raise ValueError(f"unknown variant {variant!r}")


def _sources(cfg: dict, bench: Benchmark) -> tuple[list[str], list[Path]]:
    utils = repo_path(cfg, "polybench_src") / "utilities"
    includes = ["-I", str(utils), "-I", str(bench.src_dir)]
    return includes, [utils / "polybench.c", bench.source]


def bitcode_path(cfg: dict, bench: Benchmark, variant: str) -> Path:
    return repo_path(cfg, "build_dir") / "bitcode" / f"{bench.name}.{variant}.bc"


def build_bitcode(tc: Toolchain, cfg: dict, bench: Benchmark, variant: str,
                  out: Path | None = None) -> Path:
    """Unoptimized but metadata-complete bitcode (TBAA etc.), linked into one module.

    -O3 -Xclang -disable-llvm-passes keeps the frontend metadata that -O0 drops,
    so `opt -passes=default<O3>` on it matches real `clang -O3`. Not -O1: there the
    frontend tags every loop with llvm.loop.unroll.disable.
    """
    includes, sources = _sources(cfg, bench)
    out = out or bitcode_path(cfg, bench, variant)
    parts_dir = out.parent / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for src in sources:
        part = parts_dir / f"{bench.name}.{variant}.{src.stem}.bc"
        tc.clang(["-O3", "-Xclang", "-disable-llvm-passes", "-emit-llvm", "-c",
                  *includes, *defines(cfg, bench, variant), src, "-o", part])
        parts.append(part)
    tc.llvm_link(parts, out)
    return out


def build_from_source(tc: Toolchain, cfg: dict, bench: Benchmark, variant: str,
                      opt_level: str, out_bin: Path) -> Path:
    """Plain `clang -O<n>` build straight from C, the reference our pipeline must match."""
    includes, sources = _sources(cfg, bench)
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    tc.clang([opt_level, *includes, *defines(cfg, bench, variant), *sources,
              *tc.runtime_objs(), *cfg["target"]["link_flags"], "-o", out_bin])
    return out_bin
