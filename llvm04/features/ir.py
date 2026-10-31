"""Autophase-style IR features: opcode and structure counts from textual IR.

Counts come from `llvm-dis` output, loops from `opt -passes=print<loops>`. Cheap
and deterministic; enough for an agent to tell a loop nest from straight-line
code and for the Phase 4 ranker to start from.
"""

import re
from collections import Counter
from pathlib import Path

from llvm04.compiler.toolchain import Toolchain

_DEFINE = re.compile(r"^define .*@([\w.$]+)\(")
_INSN = re.compile(r"^\s+(?:%[\w.\-]+ = )?(?:tail |musttail |notail )?([a-z][\w]*)\b")
_LABEL = re.compile(r"^[\w.\-]+:")
_LOOP = re.compile(r"^\s*Loop at depth (\d+) containing:")
_LOOP_FN = re.compile(r"^Loop info for function '(.+)':")

GROUPS = {
    "memory": {"load", "store", "alloca", "getelementptr", "fence", "atomicrmw", "cmpxchg"},
    "int_arith": {"add", "sub", "mul", "udiv", "sdiv", "urem", "srem", "shl", "lshr", "ashr",
                  "and", "or", "xor"},
    "fp_arith": {"fadd", "fsub", "fmul", "fdiv", "frem", "fneg"},
    "compare": {"icmp", "fcmp"},
    "cast": {"trunc", "zext", "sext", "fptrunc", "fpext", "fptoui", "fptosi", "uitofp",
             "sitofp", "ptrtoint", "inttoptr", "bitcast", "addrspacecast"},
    "control": {"br", "switch", "ret", "unreachable", "indirectbr"},
    "call": {"call", "invoke"},
    "phi_select": {"phi", "select"},
    "vector": {"extractelement", "insertelement", "shufflevector"},
}


def _empty() -> dict:
    return {"instructions": 0, "basic_blocks": 0, "loops": 0, "max_loop_depth": 0,
            **{g: 0 for g in GROUPS}, "vector_typed": 0, "opcodes": Counter()}


def ir_features(tc: Toolchain, bitcode: Path, top_opcodes: int = 15) -> dict:
    """Module totals plus a per-function breakdown for functions with loops or `kernel_*`."""
    text = tc._run("llvm-dis", [tc.tool("llvm-dis"), bitcode, "-o", "-"]).stdout
    funcs: dict[str, dict] = {}
    cur = None
    for line in text.splitlines():
        m = _DEFINE.match(line)
        if m:
            cur = funcs.setdefault(m[1], _empty())
            cur["basic_blocks"] = 1
            continue
        if cur is None:
            continue
        if line == "}":
            cur = None
        elif _LABEL.match(line):
            cur["basic_blocks"] += 1
        elif (m := _INSN.match(line)):
            op = m[1]
            cur["instructions"] += 1
            cur["opcodes"][op] += 1
            for g, ops in GROUPS.items():
                if op in ops:
                    cur[g] += 1
            if re.search(r"<\d+ x ", line):
                cur["vector_typed"] += 1

    loops = tc._run("opt", [tc.tool("opt"), "-passes=print<loops>", "-disable-output",
                            bitcode]).stderr
    fn = None
    for line in loops.splitlines():
        if (m := _LOOP_FN.match(line)):
            fn = funcs.get(m[1])
        elif (m := _LOOP.match(line)) and fn is not None:
            fn["loops"] += 1
            fn["max_loop_depth"] = max(fn["max_loop_depth"], int(m[1]))

    total = _empty()
    for f in funcs.values():
        for k, v in f.items():
            if k == "opcodes":
                total[k] += v
            elif k == "max_loop_depth":
                total[k] = max(total[k], v)
            else:
                total[k] += v

    def finish(d: dict) -> dict:
        return {**{k: v for k, v in d.items() if k != "opcodes"},
                "top_opcodes": dict(d["opcodes"].most_common(top_opcodes))}

    return {
        "functions": len(funcs),
        "module": finish(total),
        "by_function": {name: finish(f) for name, f in sorted(funcs.items())
                        if f["loops"] or name.startswith("kernel_")},
    }
