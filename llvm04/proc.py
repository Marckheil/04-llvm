"""Subprocess runner with a hard timeout that kills the whole process group."""

import os
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float


class ProcTimeout(Exception):
    def __init__(self, cmd: list[str], timeout_s: float):
        super().__init__(f"timed out after {timeout_s}s: {' '.join(map(str, cmd))}")
        self.cmd = cmd
        self.timeout_s = timeout_s


def run(cmd: list, timeout_s: float, cwd=None) -> ProcResult:
    # New session so a timeout also kills children (e.g. clang -> ld).
    start = time.perf_counter()
    p = subprocess.Popen(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        out, err = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise ProcTimeout(cmd, timeout_s)
    return ProcResult(p.returncode, out, err, time.perf_counter() - start)
