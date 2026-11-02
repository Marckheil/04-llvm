"""Benchmark timing and speedup statistics (macOS protocol, 04LLVM.md Section 5a)."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from o4_llvm import proc


class RunError(Exception):
    pass


def parse_polybench_time(stdout: str) -> float:
    """POLYBENCH_TIME prints the kernel time in seconds as the last stdout line."""
    lines = [l for l in stdout.strip().splitlines() if l.strip()]
    if not lines:
        raise RunError("no timing output")
    return float(lines[-1])


def time_once(binary: Path, timeout_s: float, env: dict | None = None) -> float:
    res = proc.run([binary], timeout_s, env=env)
    if res.returncode != 0:
        raise RunError(f"{binary} exited {res.returncode}: {res.stderr.strip()[:300]}")
    return parse_polybench_time(res.stdout)


def run_interleaved(binaries: dict[str, Path], warmup: int, reps: int,
                    timeout_s: float, env: dict | None = None) -> dict[str, list[float]]:
    """Time several binaries in ABBA order so thermal drift hits each one equally."""
    names = list(binaries)
    for _ in range(warmup):
        for n in names:
            time_once(binaries[n], timeout_s, env)
    samples: dict[str, list[float]] = {n: [] for n in names}
    for i in range(reps):
        for n in (names if i % 2 == 0 else names[::-1]):
            samples[n].append(time_once(binaries[n], timeout_s, env))
    return samples


def time_layouts(binary: Path, salts: list[int], reps: int,
                 timeout_s: float) -> dict[int, list[float]]:
    """Time `binary` under several array layouts (benchmarks/runtime/placement.c).

    A speedup that only holds for some salts is a layout effect, not a real win.
    """
    samples: dict[int, list[float]] = {s: [] for s in salts}
    for i in range(reps):
        for s in (salts if i % 2 == 0 else salts[::-1]):
            env = {"LLVM04_LAYOUT_SALT": str(s)}
            samples[s].append(time_once(binary, timeout_s, env))
    return samples


@dataclass
class Speedup:
    ratio: float  # baseline_median / candidate_median; > 1 means candidate is faster
    ci_lo: float
    ci_hi: float

    @property
    def significant(self) -> bool:
        return self.ci_lo > 1.0 or self.ci_hi < 1.0


def cv(samples: list[float]) -> float:
    """Noise measure: stdev relative to the median."""
    a = np.asarray(samples)
    return float(a.std(ddof=1) / np.median(a))


def speedup_ci(baseline: list[float], candidate: list[float], n_boot: int = 10000,
               seed: int = 0, alpha: float = 0.05) -> Speedup:
    """Bootstrap CI on the ratio of medians."""
    rng = np.random.default_rng(seed)
    b, c = np.asarray(baseline), np.asarray(candidate)
    b_med = np.median(rng.choice(b, (n_boot, b.size)), axis=1)
    c_med = np.median(rng.choice(c, (n_boot, c.size)), axis=1)
    lo, hi = np.quantile(b_med / c_med, [alpha / 2, 1 - alpha / 2])
    return Speedup(float(np.median(b) / np.median(c)), float(lo), float(hi))
