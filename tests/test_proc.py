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


def test_rss_cap_kills_a_memory_hog():
    hog = ["python3", "-c", "import time; x = bytearray(400 * 2**20); time.sleep(30)"]
    start = time.perf_counter()
    with pytest.raises(proc.ProcResourceLimit, match="resident memory"):
        proc.run(hog, timeout_s=20, max_rss_mb=100)
    assert time.perf_counter() - start < 10


@pytest.mark.parametrize("writer", [
    "dd if=/dev/zero of={f} bs=1048576 count=8",  # killed by SIGXFSZ, like opt and llc
    "python3 -c open('{f}','wb').write(bytes(8*2**20))",  # ignores the signal, gets EFBIG
])
def test_file_size_cap(tmp_path, writer):
    big = writer.format(f=tmp_path / "f").split()
    with pytest.raises(proc.ProcResourceLimit, match="file over"):
        proc.run(big, timeout_s=20, max_file_mb=1)
    assert proc.run(big, timeout_s=20, max_file_mb=64).returncode == 0
