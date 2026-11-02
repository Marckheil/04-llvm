"""The tool layer (04LLVM.md Section 4) as a plain Python API.

server.py exposes these methods over MCP; tests and the orchestrator call them
directly. Every tool returns a JSON-able dict, never raises:
  {"ok": true, ...}                                      on success
  {"ok": false, "error": {"code": CODE, "message": ..., ...}}  on failure
with CODE one of ERROR_CODES, so the agent can branch on it. Every call is
appended to a JSONL log (tool, args, latency, ok, code): the source of the
malformed-call-rate metric.

Episode rules (docs/decisions.md D11):
  - One episode per benchmark; `compile` spends one unit of budget per distinct
    pipeline, failures included, as in random search (D9). Recompiling a pipeline
    already tried this episode is free and returns the same build_id.
  - Invalid pipelines are rejected before compiling and cost nothing.
  - A build is timed only after it passes the correctness gate (D8).
"""

import csv
import difflib
import functools
import hashlib
import inspect
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from o4_llvm import proc
from o4_llvm.bench import correctness
from o4_llvm.bench.polybench import bitcode_path, build_bitcode, defines, list_benchmarks
from o4_llvm.bench.timing import RunError, run_interleaved, speedup_ci
from o4_llvm.compiler import introspect
from o4_llvm.compiler.pipeline import InvalidPipeline, load_passes, render
from o4_llvm.compiler.toolchain import CompileError, Toolchain
from o4_llvm.config import REPO_ROOT, repo_path
from o4_llvm.features.ir import ir_features

ERROR_CODES = ("INVALID_ARGUMENT", "INVALID_PIPELINE", "COMPILE_ERROR", "MISCOMPILE", "CRASH",
               "TIMEOUT", "RESOURCE_LIMIT", "BUDGET_EXCEEDED", "INTERNAL")
BASES = {"none": "", "O1": "default<O1>", "O2": "default<O2>", "O3": "default<O3>",
         "Os": "default<Os>", "Oz": "default<Oz>"}
LEVELS = ("O0", "O1", "O2", "O3", "Os")
REMARK_KINDS = ("missed", "passed", "analysis", "failure", "all")
MAX_PASSES = 64
MAX_REPS = 15
MAX_LIST = 50
# A timed run longer than this multiple of O3's whole-process time is killed.
SLOW_FACTOR = 4.0
BASELINES_CSV = REPO_ROOT / "results" / "summary" / "baselines.csv"


class ToolError(Exception):
    def __init__(self, code: str, message: str, **details):
        assert code in ERROR_CODES, code
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


@dataclass
class Build:
    build_id: str
    benchmark: str
    base: str
    passes: list[str]
    pipeline: str
    dir: str
    status: str = "OK"            # OK, COMPILE_ERROR, TIMEOUT, RESOURCE_LIMIT
    detail: str = ""
    compile_s: float = 0.0
    text_size: int = 0
    gate: dict | None = None      # {status, detail, bitwise, max_rel_err}

    @property
    def binary(self) -> Path:
        return Path(self.dir) / "bin"

    @property
    def check_binary(self) -> Path:
        return Path(self.dir) / "bin.check"

    @property
    def opt_bc(self) -> Path:
        return Path(self.dir) / "bin.opt.bc"


@dataclass
class Episode:
    benchmark: str
    budget: int
    tried: dict[str, dict] = field(default_factory=dict)  # build_id -> history entry

    @property
    def used(self) -> int:
        return len(self.tried)


def tool(fn):
    """Turn a method into a tool: catch typed errors, never raise, log every call."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            call_args = {k: v for k, v in sig.bind(self, *args, **kwargs).arguments.items()
                         if k != "self"}
        except TypeError as e:  # missing or unexpected argument
            out = {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(e)}}
            self._log(fn.__name__, {"args": [repr(a) for a in args], **kwargs},
                      time.perf_counter() - t0, out)
            return out
        try:
            out = {"ok": True, **fn(self, *args, **kwargs)}
        except ToolError as e:
            out = {"ok": False, "error": e.as_dict()}
        except Exception as e:  # a bug, not the agent's fault; keep the server alive
            out = {"ok": False, "error": {"code": "INTERNAL",
                                          "message": f"{type(e).__name__}: {str(e)[:300]}"}}
        self._log(fn.__name__, call_args, time.perf_counter() - t0, out)
        return out

    wrapper.is_tool = True
    return wrapper


class ToolService:
    def __init__(self, cfg: dict, work_dir: Path | None = None, log_path: Path | None = None,
                 budget: int | None = None):
        self.cfg = cfg
        self.tc = Toolchain(cfg)
        self.passes = load_passes()
        self.benches = {b.name: b for b in list_benchmarks(cfg)}
        self.work = Path(work_dir or repo_path(cfg, "build_dir") / "mcp")
        self.log_path = Path(log_path or repo_path(cfg, "results_dir") / "mcp_calls.jsonl")
        self.budget = budget or cfg["episode"]["compile_budget"]
        self.episodes: dict[str, Episode] = {}
        self.builds: dict[str, Build] = {}
        self._o3_elapsed: dict[str, float] = {}
        self._features: dict[str, dict] = {}
        self.baselines: dict[str, dict[str, dict]] = {}
        if BASELINES_CSV.exists():
            for r in csv.DictReader(open(BASELINES_CSV)):
                self.baselines.setdefault(r["benchmark"], {})[r["level"]] = r

    # ---- helpers -------------------------------------------------------------

    def _log(self, name: str, args: dict, latency: float, out: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(), "tool": name, "args": args, "latency_s": round(latency, 4),
               "ok": out["ok"], "code": None if out["ok"] else out["error"]["code"]}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def _bench(self, name):
        if not isinstance(name, str) or name not in self.benches:
            raise ToolError("INVALID_ARGUMENT", f"unknown benchmark {name!r}",
                            suggestions=difflib.get_close_matches(str(name), self.benches, n=3))
        return self.benches[name]

    def _build(self, build_id) -> Build:
        if build_id not in self.builds:
            raise ToolError("INVALID_ARGUMENT", f"unknown build_id {build_id!r}; "
                            "build ids come from compile in this session")
        return self.builds[build_id]

    def _episode(self, bench: str) -> Episode:
        return self.episodes.setdefault(bench, Episode(bench, self.budget))

    @staticmethod
    def _int_arg(name: str, v, lo: int, hi: int) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ToolError("INVALID_ARGUMENT", f"{name} must be an integer in [{lo}, {hi}], "
                            f"got {v!r}")
        return v

    def _pipeline(self, base, passes) -> str:
        if base not in BASES:
            raise ToolError("INVALID_PIPELINE", f"unknown base {base!r}",
                            allowed_bases=list(BASES))
        if not isinstance(passes, list) or not all(isinstance(p, str) for p in passes):
            raise ToolError("INVALID_PIPELINE", "passes must be a list of pass names "
                            "(see list_passes)")
        if len(passes) > MAX_PASSES:
            raise ToolError("INVALID_PIPELINE", f"{len(passes)} passes; at most {MAX_PASSES}")
        if not BASES[base] and not passes:
            raise ToolError("INVALID_PIPELINE", "empty pipeline: give passes or a base")
        try:
            suffix = render(passes, self.passes)
        except InvalidPipeline as e:
            raise ToolError("INVALID_PIPELINE", str(e), pass_name=e.name,
                            position=passes.index(e.name), suggestions=e.suggestions)
        return ",".join(p for p in (BASES[base], suffix) if p)

    def _build_id(self, bench: str, pipeline: str) -> str:
        key = {"benchmark": bench, "pipeline": pipeline, "llvm": self.tc.version,
               "codegen": [self.tc.codegen_opt, *self.tc.llc_flags],
               "defines": [defines(self.cfg, self.benches[bench], v) for v in ("time", "check")]}
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]

    def _compile(self, bench: str, base: str, passes: list[str], pipeline: str) -> tuple[Build, bool]:
        """Build both variants, or load an earlier result from disk. Returns (build, cached)."""
        build_id = self._build_id(bench, pipeline)
        if build_id in self.builds:
            return self.builds[build_id], True
        d = self.work / bench / build_id
        meta = d / "meta.json"
        if meta.exists():
            b = Build(**json.loads(meta.read_text()))
            if b.status != "OK" or (b.binary.exists() and b.check_binary.exists()):
                self.builds[build_id] = b
                return b, True
        b = Build(build_id, bench, base, list(passes), pipeline, str(d))
        bm = self.benches[bench]
        try:
            for variant in ("time", "check"):
                if not bitcode_path(self.cfg, bm, variant).exists():
                    build_bitcode(self.tc, self.cfg, bm, variant)
            t = self.tc.build(bitcode_path(self.cfg, bm, "time"), pipeline, b.binary)
            c = self.tc.build(bitcode_path(self.cfg, bm, "check"), pipeline, b.check_binary)
            b.compile_s, b.text_size = t.compile_s, t.text_size
            b.detail = f"check build {c.compile_s:.3f}s"
        except CompileError as e:
            b.status, b.detail = "COMPILE_ERROR", f"{e.stage}: {e.stderr.strip()[-500:]}"
        except proc.ProcTimeout:
            b.status, b.detail = "TIMEOUT", f"compile over {self.tc.timeout_s}s"
        except proc.ProcResourceLimit as e:
            b.status, b.detail = "RESOURCE_LIMIT", f"compile: {e.what}"
        self._save(b)
        self.builds[build_id] = b
        return b, False

    def _save(self, b: Build) -> None:
        Path(b.dir).mkdir(parents=True, exist_ok=True)
        (Path(b.dir) / "meta.json").write_text(json.dumps(asdict(b), indent=1))

    def _gate(self, b: Build) -> dict:
        if b.status != "OK":
            raise ToolError("INVALID_ARGUMENT", f"build {b.build_id} did not compile "
                            f"({b.status}); nothing to check")
        if b.gate is None:
            g = correctness.check(self.tc, self.cfg, self.benches[b.benchmark], b.check_binary)
            b.gate = {"status": g.status, "detail": g.detail, "bitwise": g.bitwise,
                      "max_rel_err": g.max_rel_err}
            self._save(b)
            ep = self._episode(b.benchmark)
            if b.build_id in ep.tried:
                ep.tried[b.build_id]["gate"] = g.status
        return b.gate

    def _o3(self, bench: str) -> tuple[Build, float]:
        """The default<O3> reference build and its whole-process time (not charged)."""
        b, _ = self._compile(bench, "O3", [], BASES["O3"])
        if b.status != "OK":
            raise ToolError("INTERNAL", f"default<O3> failed to build for {bench}: {b.detail}")
        if bench not in self._o3_elapsed:
            self._o3_elapsed[bench] = proc.run([b.binary], self.cfg["limits"]["run_timeout_s"]
                                               ).elapsed_s
        return b, self._o3_elapsed[bench]

    # ---- tools ---------------------------------------------------------------

    @tool
    def list_benchmarks(self, category: str | None = None) -> dict:
        """Benchmarks with their category, timed dataset and default<O3> runtime."""
        pb = self.cfg["polybench"]
        root = repo_path(self.cfg, "polybench_src")
        out = []
        for name, b in self.benches.items():
            cat = str(b.src_dir.parent.relative_to(root))
            if category and category not in cat:
                continue
            o3 = self.baselines.get(name, {}).get("O3")
            out.append({"name": name, "category": cat,
                        "dataset": pb["dataset_overrides"].get(name, pb["time_dataset"]),
                        "O3_median_s": float(o3["median_s"]) if o3 else None})
        return {"benchmarks": out, "compile_budget_per_episode": self.budget}

    @tool
    def list_passes(self) -> dict:
        """The action space: pass names by level, and the allowed `base` pipelines."""
        by_kind: dict[str, list[str]] = {}
        for name, kind in self.passes.items():
            by_kind.setdefault(kind, []).append(name)
        return {"passes": by_kind, "bases": BASES, "max_passes": MAX_PASSES,
                "note": "compile runs `base` then `passes` in order; each pass is nested at "
                        "its own level, e.g. licm -> function(loop-mssa(licm))."}

    @tool
    def get_baseline(self, benchmark: str, level: str = "O3") -> dict:
        """Phase 1 measurements of default<level> (5 interleaved runs, gated)."""
        self._bench(benchmark)
        if level not in LEVELS:
            raise ToolError("INVALID_ARGUMENT", f"level must be one of {list(LEVELS)}")
        r = self.baselines.get(benchmark, {}).get(level)
        if r is None:
            raise ToolError("INVALID_ARGUMENT", f"no recorded baseline for {benchmark} {level}; "
                            "run experiments/baselines.py")
        return {"benchmark": benchmark, "level": level, "gate": r["gate"],
                "median_s": float(r["median_s"]), "cv": float(r["cv"]),
                "speedup_vs_O3": float(r["speedup_vs_O3"]),
                "ci95": [float(r["ci_lo"]), float(r["ci_hi"])],
                "compile_s": float(r["compile_s"]), "text_size": int(r["text_size"])}

    @tool
    def get_ir_features(self, benchmark: str | None = None, build_id: str | None = None) -> dict:
        """Opcode and loop counts of a benchmark's input IR, or of a build's optimized IR."""
        if (benchmark is None) == (build_id is None):
            raise ToolError("INVALID_ARGUMENT", "give exactly one of benchmark or build_id")
        if build_id is not None:
            b = self._build(build_id)
            if b.status != "OK":
                raise ToolError("INVALID_ARGUMENT", f"build {build_id} did not compile")
            key, bc, what = build_id, b.opt_bc, f"optimized IR of build {build_id}"
        else:
            bm = self._bench(benchmark)
            bc = bitcode_path(self.cfg, bm, "time")
            if not bc.exists():
                build_bitcode(self.tc, self.cfg, bm, "time")
            key, what = f"input/{benchmark}", "unoptimized input IR (allocas not yet promoted)"
        if key not in self._features:
            self._features[key] = ir_features(self.tc, bc)
        return {"source": what, **self._features[key]}

    @tool
    def compile(self, benchmark: str, passes: list[str], base: str = "O3",
                rationale: str | None = None) -> dict:
        """Compile `base` followed by `passes`; costs one unit of budget unless already tried.

        `rationale` (why the agent expects this pipeline to help) is only recorded.
        """
        self._bench(benchmark)
        pipeline = self._pipeline(base, passes)
        ep = self._episode(benchmark)
        build_id = self._build_id(benchmark, pipeline)
        repeat = build_id in ep.tried
        if not repeat and ep.used >= ep.budget:
            raise ToolError("BUDGET_EXCEEDED", f"all {ep.budget} compiles of this episode are "
                            "spent", budget_used=ep.used, budget=ep.budget)
        b, cached = self._compile(benchmark, base, passes, pipeline)
        if not repeat:
            ep.tried[build_id] = {"build_id": build_id, "base": base, "passes": list(passes),
                                  "status": b.status, "compile_s": round(b.compile_s, 4),
                                  "text_size": b.text_size,
                                  "gate": b.gate["status"] if b.gate else None,
                                  "rationale": (rationale or "")[:500]}
        budget = {"budget_used": ep.used, "budget_remaining": ep.budget - ep.used,
                  "charged": not repeat}
        if b.status != "OK":
            raise ToolError(b.status, b.detail, build_id=build_id, **budget)
        o3 = self.baselines.get(benchmark, {}).get("O3")
        return {"build_id": build_id, "pipeline": b.pipeline, "compile_s": round(b.compile_s, 4),
                "text_size": b.text_size,
                "text_size_vs_O3": round(b.text_size / int(o3["text_size"]), 4) if o3 else None,
                "cached": cached, **budget}

    @tool
    def check_correctness(self, build_id: str) -> dict:
        """Run the build's array-dump variant and compare with the clang -O0 reference (D8)."""
        b = self._build(build_id)
        g = self._gate(b)
        return {"build_id": build_id, "status": g["status"], "bitwise_identical": g["bitwise"],
                "max_scaled_error": g["max_rel_err"],
                "tolerance": self.cfg["correctness"]["rtol"], "detail": g["detail"]}

    @tool
    def run_benchmark(self, build_id: str, repetitions: int = 5,
                      layout_check: bool = False) -> dict:
        """Time a gated build against default<O3> (interleaved, bootstrap CI on the median ratio)."""
        b = self._build(build_id)
        reps = self._int_arg("repetitions", repetitions, 1, MAX_REPS)
        g = self._gate(b)
        if g["status"] != correctness.PASS:
            raise ToolError(g["status"], "refusing to time a build that fails the correctness "
                            f"gate: {g['detail']}", build_id=build_id)
        o3, o3_elapsed = self._o3(b.benchmark)
        timeout = max(10.0, SLOW_FACTOR * o3_elapsed)
        bins = {"candidate": b.binary, "O3": o3.binary}
        s, t = self.cfg["search"], self.cfg["timing"]
        try:
            samp = run_interleaved(bins, 1, reps, timeout)
            salts = {}
            if layout_check:
                for salt in s["layout_salts"]:
                    ls = run_interleaved(bins, 0, s["layout_reps"], timeout,
                                         env={"LLVM04_LAYOUT_SALT": str(salt)})
                    salts[str(salt)] = round(float(np.median(ls["O3"]) /
                                                   np.median(ls["candidate"])), 4)
        except proc.ProcTimeout:
            raise ToolError("TIMEOUT", f"a run took over {timeout:.1f}s "
                            f"(>{SLOW_FACTOR:g}x default<O3>)", build_id=build_id)
        except RunError as e:
            raise ToolError("CRASH", str(e)[:400], build_id=build_id)
        sp = speedup_ci(samp["O3"], samp["candidate"], t["bootstrap_samples"], self.cfg["seeds"][0])
        verdict = "FASTER" if sp.ci_lo > 1 else "SLOWER" if sp.ci_hi < 1 else "TIE"
        c = np.asarray(samp["candidate"])
        out = {"build_id": build_id, "repetitions": reps,
               "median_s": round(float(np.median(c)), 6),
               "stdev_s": round(float(c.std(ddof=1)), 6) if reps > 1 else None,
               "O3_median_s": round(float(np.median(samp["O3"])), 6),
               "speedup_vs_O3": round(sp.ratio, 4),
               "ci95": [round(sp.ci_lo, 4), round(sp.ci_hi, 4)], "verdict": verdict}
        if layout_check:
            out["layout_speedups"] = salts
            out["layout_robust"] = min(salts.values()) > 1
        ep = self._episode(b.benchmark)
        if build_id in ep.tried:
            ep.tried[build_id].update(speedup_vs_O3=out["speedup_vs_O3"], ci95=out["ci95"],
                                      verdict=verdict)
            if layout_check:
                ep.tried[build_id]["layout_robust"] = out["layout_robust"]
        return out

    @tool
    def get_remarks(self, build_id: str, kind: str = "missed", pass_name: str | None = None,
                    function: str | None = None, limit: int = 20) -> dict:
        """Optimization remarks of the build's pipeline, grouped and truncated."""
        b = self._build(build_id)
        if kind not in REMARK_KINDS:
            raise ToolError("INVALID_ARGUMENT", f"kind must be one of {list(REMARK_KINDS)}")
        limit = self._int_arg("limit", limit, 1, MAX_LIST)
        bc = bitcode_path(self.cfg, self.benches[b.benchmark], "time")
        try:
            rs = introspect.remarks(self.tc, bc, b.pipeline, Path(b.dir) / "remarks.yaml")
        except CompileError as e:
            raise ToolError("COMPILE_ERROR", f"{e.stage}: {e.stderr.strip()[-500:]}")
        return {"build_id": build_id,
                **introspect.summarize_remarks(rs, kind, pass_name, function, limit)}

    @tool
    def get_pass_timing(self, build_id: str, top: int = 20) -> dict:
        """Per-pass wall time of the build's pipeline from `opt -time-passes`."""
        b = self._build(build_id)
        top = self._int_arg("top", top, 1, MAX_LIST)
        bc = bitcode_path(self.cfg, self.benches[b.benchmark], "time")
        try:
            return {"build_id": build_id, **introspect.pass_timing(self.tc, bc, b.pipeline, top)}
        except CompileError as e:
            raise ToolError("COMPILE_ERROR", f"{e.stage}: {e.stderr.strip()[-500:]}")

    @tool
    def get_history(self, benchmark: str) -> dict:
        """Everything compiled this episode, in order, plus the budget and the best gated result."""
        self._bench(benchmark)
        ep = self._episode(benchmark)
        timed = [h for h in ep.tried.values() if "speedup_vs_O3" in h]
        best = max(timed, key=lambda h: h["speedup_vs_O3"], default=None)
        return {"benchmark": benchmark, "budget": ep.budget, "budget_used": ep.used,
                "budget_remaining": ep.budget - ep.used, "candidates": list(ep.tried.values()),
                "best": best}

    def tools(self) -> list[str]:
        return [n for n, v in inspect.getmembers(type(self)) if getattr(v, "is_tool", False)]
