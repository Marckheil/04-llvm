import time

import pytest

from llvm04 import proc


def test_run_captures_output():
    res = proc.run(["sh", "-c", "echo hi; echo err >&2; exit 3"], timeout_s=5)
    assert (res.returncode, res.stdout, res.stderr) == (3, "hi\n", "err\n")


def test_timeout_kills_process_group():
    start = time.perf_counter()
    # The child sleep would outlive a plain kill of the parent shell.
    with pytest.raises(proc.ProcTimeout):
        proc.run(["sh", "-c", "sleep 30 & wait"], timeout_s=0.5)
    assert time.perf_counter() - start < 5
