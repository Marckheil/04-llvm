"""Subprocess runner with a hard timeout that kills the whole process group.

Optional resource caps for untrusted work (compiling arbitrary pipelines):
  max_rss_mb   macOS ignores RLIMIT_AS/RLIMIT_DATA, so a watchdog thread polls the
               group's resident memory with psutil and kills it past the cap.
  max_file_mb  RLIMIT_FSIZE, which macOS does enforce: a write past it raises
               SIGXFSZ in the child.
Either one tripping raises ProcResourceLimit.
"""

import os
import resource
import signal
import subprocess
import threading
import time
from dataclasses import dataclass

import psutil

RSS_POLL_S = 0.05


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


class ProcResourceLimit(Exception):
    def __init__(self, cmd: list[str], what: str):
        super().__init__(f"{what}: {' '.join(map(str, cmd))}")
        self.cmd = cmd
        self.what = what


def _kill_group(p: subprocess.Popen) -> None:
    # The child may exit between the timeout and the kill; macOS then reports
    # EPERM (zombie group leader) rather than ESRCH.
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        p.kill()


def _group_rss(pid: int) -> int:
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0
    total = 0
    for q in procs:
        try:
            total += q.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def run(cmd: list, timeout_s: float, cwd=None, env: dict | None = None,
        max_rss_mb: float | None = None, max_file_mb: float | None = None) -> ProcResult:
    def limits():
        if max_file_mb is not None:
            n = int(max_file_mb * 2**20)
            resource.setrlimit(resource.RLIMIT_FSIZE, (n, n))

    # New session so a timeout also kills children (e.g. clang -> ld).
    start = time.perf_counter()
    p = subprocess.Popen(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
        start_new_session=True,
        preexec_fn=limits if max_file_mb is not None else None,
    )
    over_rss = threading.Event()
    if max_rss_mb is not None:
        cap = max_rss_mb * 2**20

        def watch():
            while p.poll() is None:
                if _group_rss(p.pid) > cap:
                    over_rss.set()
                    _kill_group(p)
                    return
                time.sleep(RSS_POLL_S)

        threading.Thread(target=watch, daemon=True).start()
    try:
        out, err = p.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        p.communicate()
        raise ProcTimeout(cmd, timeout_s)
    if over_rss.is_set():
        raise ProcResourceLimit(cmd, f"resident memory over {max_rss_mb} MB")
    # Killed by SIGXFSZ directly; via a driver (clang reports its linker's signal);
    # or, if the child ignores the signal, failed with EFBIG.
    if max_file_mb is not None and p.returncode != 0 and (
            p.returncode == -signal.SIGXFSZ
            or any(m in err for m in ("Filesize limit exceeded", "File too large"))):
        raise ProcResourceLimit(cmd, f"wrote a file over {max_file_mb} MB")
    return ProcResult(p.returncode, out, err, time.perf_counter() - start)
