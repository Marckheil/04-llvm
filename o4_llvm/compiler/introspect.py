"""What did a pipeline do? Optimization remarks and per-pass timing from `opt`.

Both re-run `opt` on demand with the build's pipeline, so plain compiles are not
slowed down (and their compile_s is not inflated) by remark or timer output.
Results are summarized and bounded: raw remarks for one module run to megabytes.
"""

import re
from collections import Counter
from pathlib import Path

import yaml

from o4_llvm.compiler.toolchain import Toolchain

REMARK_KINDS = {"Passed": "passed", "Missed": "missed", "Analysis": "analysis",
                "AnalysisFPCommute": "analysis", "AnalysisAliasing": "analysis",
                "Failure": "failure"}

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _RemarkLoader(_Loader):
    pass


_RemarkLoader.add_multi_constructor(
    "!", lambda loader, tag, node: {"_kind": tag, **loader.construct_mapping(node, deep=True)})


def _message(args: list) -> str:
    parts = []
    for a in args or []:
        if isinstance(a, dict):
            parts.extend(str(v) for k, v in a.items() if k != "DebugLoc")
    return "".join(parts)


def remarks(tc: Toolchain, in_bc: Path, pipeline: str, out_yaml: Path) -> list[dict]:
    """All remarks of one `opt` run as {kind, pass, name, function, message}."""
    if not out_yaml.exists():
        tmp = out_yaml.with_name(out_yaml.name + ".tmp")
        tc._run("opt", [tc.tool("opt"), f"-passes={pipeline}", in_bc, "-disable-output",
                        f"-pass-remarks-output={tmp}"])
        tmp.rename(out_yaml)
    out = []
    with open(out_yaml) as f:
        for doc in yaml.load_all(f, Loader=_RemarkLoader):
            if not doc:
                continue
            out.append({"kind": REMARK_KINDS.get(doc["_kind"], doc["_kind"].lower()),
                        "pass": doc.get("Pass", ""), "name": doc.get("Name", ""),
                        "function": doc.get("Function", ""),
                        "message": _message(doc.get("Args"))})
    return out


def summarize_remarks(rs: list[dict], kind: str | None, pass_name: str | None,
                      function: str | None, limit: int, msg_chars: int = 240) -> dict:
    """Group remarks by (kind, pass, name, function); keep the `limit` largest groups."""
    totals = Counter(r["kind"] for r in rs)
    sel = [r for r in rs if (kind in (None, "all") or r["kind"] == kind)
           and (pass_name is None or r["pass"] == pass_name)
           and (function is None or r["function"] == function)]
    groups: dict[tuple, dict] = {}
    for r in sel:
        key = (r["kind"], r["pass"], r["name"], r["function"])
        g = groups.setdefault(key, {"kind": r["kind"], "pass": r["pass"], "name": r["name"],
                                    "function": r["function"], "count": 0, "example": ""})
        g["count"] += 1
        if not g["example"]:
            m = r["message"]
            g["example"] = m if len(m) <= msg_chars else m[:msg_chars] + "..."
    ranked = sorted(groups.values(), key=lambda g: (-g["count"], g["pass"], g["name"]))
    return {"totals_by_kind": dict(totals), "matched": len(sel), "groups": ranked[:limit],
            "groups_omitted": max(0, len(ranked) - limit),
            "passes_with_remarks": sorted({r["pass"] for r in sel})}


# "   0.0005 ( 38.5%)   ...   0.0007 ( 40.3%)    7319748  TargetIRAnalysis": up to four
# (seconds, percent) columns, wall time last, then (LLVM >= 19) retired instructions.
_TIME_ROW = re.compile(r"^\s*((?:[\d.]+ \(\s*[\d.]+%\)\s+)+)(?:(\d+)\s+)?(\S.*)$")
_TIME_COL = re.compile(r"([\d.]+) \(")


def pass_timing(tc: Toolchain, in_bc: Path, pipeline: str, top: int) -> dict:
    """Per-pass wall time and instruction count from `opt -time-passes` (passes, not analyses).

    Instruction counts are deterministic; wall times of a few ms are mostly noise.
    """
    res = tc._run("opt", [tc.tool("opt"), f"-passes={pipeline}", in_bc, "-disable-output",
                          "-time-passes"])
    section, wall, instr = None, Counter(), Counter()
    for line in res.stderr.splitlines():
        if "Pass execution timing report" in line:
            section = "pass"
        elif "Analysis execution timing report" in line:
            section = "analysis"
        elif section == "pass" and (m := _TIME_ROW.match(line)):
            name = m[3].strip()
            if name != "Total":
                wall[name] += float(_TIME_COL.findall(m[1])[-1])
                instr[name] += int(m[2] or 0)
    ranked = sorted(wall, key=lambda n: (-instr[n], -wall[n]))
    return {"total_wall_s": round(sum(wall.values()), 6),
            "total_instructions": sum(instr.values()),
            "passes": [{"pass": n, "wall_s": round(wall[n], 6), "instructions": instr[n]}
                       for n in ranked[:top]],
            "passes_omitted": max(0, len(ranked) - top)}
