import json

import pytest

from llvm04.bench import correctness
from llvm04.config import load_config
from llvm04.tools.service import ERROR_CODES, ToolService

# Rejected by LLVM 22 itself: standalone instcombine verifies it reached a fixpoint.
LLVM_REJECTS = ["licm", "loop-distribute", "loop-load-elim", "dse", "nary-reassociate",
                "argpromotion", "mldst-motion", "instcombine", "function-attrs", "gvn-hoist",
                "loop-simplify"]


@pytest.fixture
def svc(tmp_path):
    return ToolService(load_config(), work_dir=tmp_path / "work", log_path=tmp_path / "calls.jsonl",
                       budget=3)


def err(out):
    assert out["ok"] is False and out["error"]["code"] in ERROR_CODES
    return out["error"]


def test_list_tools(svc):
    assert set(svc.tools()) == {"list_benchmarks", "list_passes", "get_baseline",
                                "get_ir_features", "compile", "check_correctness",
                                "run_benchmark", "get_remarks", "get_pass_timing", "get_history"}


def test_list_benchmarks_and_filter(svc):
    out = svc.list_benchmarks()
    assert out["ok"] and len(out["benchmarks"]) == 22
    assert all(b["O3_median_s"] for b in out["benchmarks"])
    names = {b["name"] for b in svc.list_benchmarks("stencils")["benchmarks"]}
    assert "jacobi-2d" in names and "gemm" not in names


def test_unknown_benchmark_suggests(svc):
    e = err(svc.get_baseline("jacobi2d"))
    assert e["code"] == "INVALID_ARGUMENT" and "jacobi-2d" in e["suggestions"]


def test_baseline(svc):
    out = svc.get_baseline("gemm", "O1")
    assert out["ok"] and out["speedup_vs_O3"] < 1
    assert err(svc.get_baseline("gemm", "O9"))["code"] == "INVALID_ARGUMENT"


def test_invalid_pipeline_is_free_and_explained(svc):
    e = err(svc.compile("jacobi-2d", ["licm", "separate-const-offset-from-gp"]))
    assert e["code"] == "INVALID_PIPELINE" and e["position"] == 1
    assert "separate-const-offset-from-gep" in e["suggestions"]
    assert err(svc.compile("jacobi-2d", ["licm"], base="O4"))["code"] == "INVALID_PIPELINE"
    assert err(svc.compile("jacobi-2d", [], base="none"))["code"] == "INVALID_PIPELINE"
    assert err(svc.compile("jacobi-2d", "licm"))["code"] == "INVALID_PIPELINE"
    assert svc.get_history("jacobi-2d")["budget_used"] == 0


def test_missing_argument_is_typed(svc):
    assert err(svc.compile("jacobi-2d"))["code"] == "INVALID_ARGUMENT"
    assert err(svc.check_correctness(build_id="nope"))["code"] == "INVALID_ARGUMENT"


def test_compile_gate_and_budget(svc):
    a = svc.compile("jacobi-2d", ["separate-const-offset-from-gep"])
    assert a["ok"] and a["budget_used"] == 1 and a["charged"]
    again = svc.compile("jacobi-2d", ["separate-const-offset-from-gep"])
    assert again["build_id"] == a["build_id"] and not again["charged"]
    assert again["budget_used"] == 1

    g = svc.check_correctness(a["build_id"])
    assert g["ok"] and g["status"] == "PASS"

    e = err(svc.compile("2mm", LLVM_REJECTS, base="none"))
    assert e["code"] == "COMPILE_ERROR" and "fixpoint" in e["message"]
    assert e["budget_used"] == 1  # 2mm has its own episode
    assert err(svc.compile("2mm", LLVM_REJECTS, base="none"))["charged"] is False

    assert svc.compile("jacobi-2d", ["licm"])["budget_used"] == 2
    assert svc.compile("jacobi-2d", [], base="O2")["budget_used"] == 3
    e = err(svc.compile("jacobi-2d", ["gvn"]))
    assert e["code"] == "BUDGET_EXCEEDED" and e["budget"] == 3

    h = svc.get_history("jacobi-2d")
    assert [c["passes"] for c in h["candidates"]] == [["separate-const-offset-from-gep"],
                                                      ["licm"], []]
    assert h["candidates"][0]["gate"] == "PASS" and h["budget_remaining"] == 0


def test_build_cache_survives_a_new_session(svc, tmp_path):
    a = svc.compile("jacobi-2d", ["licm"])
    svc2 = ToolService(load_config(), work_dir=tmp_path / "work", log_path=tmp_path / "c2.jsonl")
    b = svc2.compile("jacobi-2d", ["licm"])
    assert b["build_id"] == a["build_id"] and b["cached"] and b["charged"]


def test_miscompile_is_never_timed(svc, monkeypatch):
    a = svc.compile("jacobi-2d", ["licm"])
    monkeypatch.setattr(correctness, "check",
                        lambda *args: correctness.GateResult("MISCOMPILE", "array 0 differs"))
    assert svc.check_correctness(a["build_id"])["status"] == "MISCOMPILE"
    e = err(svc.run_benchmark(a["build_id"], 1))
    assert e["code"] == "MISCOMPILE" and "refusing" in e["message"]


def test_run_benchmark(svc):
    a = svc.compile("jacobi-2d", [], base="O3")
    for bad in (0, 16, "5", True):
        assert err(svc.run_benchmark(a["build_id"], bad))["code"] == "INVALID_ARGUMENT"
    out = svc.run_benchmark(a["build_id"], 2)
    assert out["ok"] and out["verdict"] in ("FASTER", "TIE", "SLOWER")
    assert 0.8 < out["speedup_vs_O3"] < 1.25  # it is O3
    assert svc.get_history("jacobi-2d")["best"]["build_id"] == a["build_id"]


def test_timeout_is_typed(svc, monkeypatch):
    import llvm04.tools.service as service
    a = svc.compile("jacobi-2d", [], base="O1")
    monkeypatch.setattr(service, "SLOW_FACTOR", 0.0)
    svc._o3_elapsed["jacobi-2d"] = 0.0
    monkeypatch.setattr(service, "run_interleaved",
                        lambda *a, **k: (_ for _ in ()).throw(service.proc.ProcTimeout(["x"], 1)))
    assert err(svc.run_benchmark(a["build_id"], 1))["code"] == "TIMEOUT"


def test_features_remarks_timing(svc):
    f = svc.get_ir_features(benchmark="jacobi-2d")
    k = f["by_function"]["kernel_jacobi_2d"]
    assert f["ok"] and k["loops"] == 5 and k["max_loop_depth"] == 3 and k["fp_arith"] > 0
    assert err(svc.get_ir_features())["code"] == "INVALID_ARGUMENT"

    a = svc.compile("jacobi-2d", [], base="O3")
    opt = svc.get_ir_features(build_id=a["build_id"])
    assert opt["module"]["vector_typed"] > 0  # O3 vectorized it

    r = svc.get_remarks(a["build_id"], kind="all", limit=3)
    assert r["ok"] and len(r["groups"]) <= 3 and r["matched"] > 0
    assert all(len(g["example"]) <= 243 for g in r["groups"])
    lv = svc.get_remarks(a["build_id"], kind="passed", pass_name="loop-vectorize")
    assert lv["groups"] and all(g["pass"] == "loop-vectorize" for g in lv["groups"])
    assert err(svc.get_remarks(a["build_id"], kind="bogus"))["code"] == "INVALID_ARGUMENT"

    t = svc.get_pass_timing(a["build_id"], top=5)
    assert t["ok"] and len(t["passes"]) == 5 and t["total_wall_s"] > 0


def test_every_call_is_logged(svc, tmp_path):
    svc.list_passes()
    svc.compile("jacobi-2d", ["nope"])
    log = [json.loads(l) for l in open(tmp_path / "calls.jsonl")]
    assert [(r["tool"], r["ok"], r["code"]) for r in log] == [
        ("list_passes", True, None), ("compile", False, "INVALID_PIPELINE")]
    assert log[1]["args"] == {"benchmark": "jacobi-2d", "passes": ["nope"]}
