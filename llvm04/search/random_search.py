"""Random search baseline (Phase 1): random valid pipelines, fixed compile budget."""

import random
from collections.abc import Callable
from pathlib import Path

from llvm04.bench.polybench import Benchmark
from llvm04.compiler.pipeline import render
from llvm04.compiler.toolchain import Toolchain
from llvm04.search.candidate import OK, Candidate, evaluate


def sample_pipeline(rng: random.Random, passes: dict[str, str], min_len: int,
                    max_len: int) -> list[str]:
    """Uniform length in [min_len, max_len], passes drawn uniformly with replacement."""
    names = sorted(passes)
    return [rng.choice(names) for _ in range(rng.randint(min_len, max_len))]


def random_search(tc: Toolchain, cfg: dict, bench: Benchmark, passes: dict[str, str],
                  mode: str, seed: int, budget: int, slow_cap_s: float, work: Path,
                  on_candidate: Callable[[int, list[str], Candidate], None] | None = None,
                  ) -> tuple[list[str] | None, Candidate | None]:
    """Spend `budget` compiles on random pipelines; return the fastest correct one.

    `mode` is a key of cfg["search"]["modes"]: a fixed pipeline prefix plus the
    length range of the random part. Ranking uses one screening run per candidate;
    the caller confirms the winner with proper repeated timing. Every attempt,
    failed or not, costs one compile.
    """
    s, m = cfg["search"], cfg["search"]["modes"][mode]
    rng = random.Random(f"{bench.name}/{mode}/{seed}")
    best_names, best = None, None
    for i in range(budget):
        names = sample_pipeline(rng, passes, m["min_len"], m["max_len"])
        pipeline = ",".join(p for p in (m["prefix"], render(names, passes)) if p)
        c = evaluate(tc, cfg, bench, pipeline, work / f"c{i:03d}", s["screen_reps"], slow_cap_s)
        if on_candidate:
            on_candidate(i, names, c)
        if c.status == OK and c.screen_best is not None and (
                best is None or c.screen_best < best.screen_best):
            best_names, best = names, c
    return best_names, best
