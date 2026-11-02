import shutil

import pytest

from o4_llvm.compiler.toolchain import Toolchain
from o4_llvm.config import load_config
from o4_llvm import proc

PROG = r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
int main(void) {
  void *a, *b, *c;
  posix_memalign(&a, 4096, 100000);
  posix_memalign(&b, 4096, 2160);
  posix_memalign(&c, 4096, 583200);
  void *m = malloc(64);  /* system heap, must still free cleanly */
  printf("%lu %lu %lu %lu\n", (unsigned long)((uintptr_t)a % (16 << 20)),
         (unsigned long)((char *)b - (char *)a), (unsigned long)((char *)c - (char *)b),
         (unsigned long)((uintptr_t)a % 4096));
  free(m); free(a); free(b); free(c);
  return 0;
}
"""


@pytest.fixture(scope="module")
def prog(tmp_path_factory):
    cfg = load_config()
    tc = Toolchain(cfg)
    if not tc.bin_dir.exists():
        pytest.skip("pinned LLVM not installed")
    d = tmp_path_factory.mktemp("placement")
    src = d / "prog.c"
    src.write_text(PROG)
    obj = d / "prog.o"
    tc.clang(["-O1", "-c", src, "-o", obj])
    out = d / "prog"
    tc.link([obj], out)
    return out


def run(prog, salt=None):
    res = proc.run([prog], 30, env={"LLVM04_LAYOUT_SALT": str(salt)} if salt is not None else None)
    assert res.returncode == 0, res.stderr
    return [int(x) for x in res.stdout.split()]


def test_layout_is_deterministic_and_page_aligned(prog):
    first = run(prog)
    assert all(run(prog) == first for _ in range(3))
    arena_off, ab, bc, page_off = first
    assert arena_off == 0 and page_off == 0  # first block at the 16 MB-aligned arena start
    assert ab >= 100000 and bc >= 2160  # in allocation order, no overlap
    assert ab % 4096 == 0 and bc % 4096 == 0


def test_salt_changes_relative_layout(prog):
    base = run(prog, 0)
    salted = run(prog, 1)
    assert salted[1:3] != base[1:3]
    assert salted[1] % 4096 == 0 and salted[2] % 4096 == 0
