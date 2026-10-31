import random

import pytest

from llvm04 import proc
from llvm04.compiler.pipeline import InvalidPipeline, load_passes, render
from llvm04.compiler.toolchain import Toolchain
from llvm04.config import load_config
from llvm04.search.random_search import sample_pipeline

PASSES = load_passes()


def test_render_nests_each_pass_at_its_level():
    s = render(["sroa", "licm", "inline", "globalopt"], PASSES)
    assert s == "function(sroa),function(loop-mssa(licm)),cgscc(inline),globalopt"


def test_unknown_pass_suggests_close_names():
    with pytest.raises(InvalidPipeline) as e:
        render(["instcombien"], PASSES)
    assert "instcombine" in e.value.suggestions


def test_sampling_is_deterministic_per_seed():
    a = sample_pipeline(random.Random(7), PASSES, 5, 40)
    b = sample_pipeline(random.Random(7), PASSES, 5, 40)
    assert a == b and 5 <= len(a) <= 40


def test_every_pass_runs_on_pinned_opt(tmp_path):
    cfg = load_config()
    tc = Toolchain(cfg)
    if not tc.bin_dir.exists():
        pytest.skip("pinned LLVM not installed")
    ll = tmp_path / "m.ll"
    ll.write_text("define double @f(ptr %a, i64 %n) {\n"
                  "entry:\n  br label %loop\n"
                  "loop:\n  %i = phi i64 [0, %entry], [%i1, %loop]\n"
                  "  %s = phi double [0.0, %entry], [%s1, %loop]\n"
                  "  %p = getelementptr double, ptr %a, i64 %i\n"
                  "  %x = load double, ptr %p\n  %s1 = fadd double %s, %x\n"
                  "  %i1 = add i64 %i, 1\n  %c = icmp slt i64 %i1, %n\n"
                  "  br i1 %c, label %loop, label %exit\n"
                  "exit:\n  ret double %s1\n}\n")
    res = proc.run([tc.tool("opt"), f"-passes={render(sorted(PASSES), PASSES)}", ll,
                    "-disable-output"], 60)
    assert res.returncode == 0, res.stderr[-500:]
