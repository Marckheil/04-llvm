"""Compile, gate and screen one candidate pipeline for one benchmark.

Statuses follow the tool-layer error types in 04LLVM.md Section 4:
OK, COMPILE_ERROR, TIMEOUT, MISCOMPILE, CRASH.
"""

from dataclasses import dataclass, field
from pathlib import Path

from llvm04 import proc
from llvm04.bench import correctness
from llvm04.bench.polybench import Benchmark, bitcode_path
from llvm04.bench.timing import RunError, time_once
from llvm04.compiler.toolchain import CompileError, Toolchain

OK, COMPILE_ERROR, TIMEOUT = "OK", "COMPILE_ERROR", "TIMEOUT"


@dataclass
class Candidate:
    pipeline: str
    status: str = OK
    detail: str = ""
    binary: Path | None = None
    compile_s: float = 0.0
    text_size: int = 0
    gate_max_rel_err: float = 0.0
    # Single screening runs; None if the run hit the slow cap.
    screen_s: list[float | None] = field(default_factory=list)

    @property
    def screen_best(self) -> float | None:
        done = [t for t in self.screen_s if t is not None]
        return min(done) if done else None


def evaluate(tc: Toolchain, cfg: dict, bench: Benchmark, pipeline: str, out: Path,
             screen_reps: int, slow_cap_s: float) -> Candidate:
    """Build `pipeline` for both variants, gate it, then time it `screen_reps` times.

    A screening run longer than `slow_cap_s` (whole-process wall time, init
    included) is killed and recorded as None: the candidate is correct but too
    slow to matter, and waiting for it wastes time.
    """
    c = Candidate(pipeline)
    try:
        build = tc.build(bitcode_path(cfg, bench, "time"), pipeline, out)
        check = tc.build(bitcode_path(cfg, bench, "check"), pipeline,
                         out.with_name(out.name + ".check"))
    except CompileError as e:
        c.status, c.detail = COMPILE_ERROR, f"{e.stage}: {e.stderr.strip()[-300:]}"
        return c
    except proc.ProcTimeout as e:
        c.status, c.detail = TIMEOUT, f"compile: {e}"
        return c
    c.binary, c.compile_s, c.text_size = build.binary, build.compile_s, build.text_size

    gate = correctness.check(tc, cfg, bench, check.binary)
    c.gate_max_rel_err = gate.max_rel_err
    if not gate.ok:
        c.status, c.detail = gate.status, gate.detail
        return c

    for _ in range(screen_reps):
        try:
            c.screen_s.append(time_once(build.binary, slow_cap_s))
        except proc.ProcTimeout:
            c.screen_s.append(None)
            break
        except RunError as e:
            c.status, c.detail = correctness.CRASH, str(e)
            return c
    return c
