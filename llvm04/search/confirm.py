"""The confirmation protocol every search method's final answer goes through (D9).

Whatever a method measured while searching, its reported number comes from here:
the candidate is re-timed against default<O3> (search.confirm_reps interleaved runs,
bootstrap CI on the ratio of medians) and, if the CI says faster, again under every
layout salt (D7). Verdicts:
  WIN          CI above 1 and faster under every salt
  LAYOUT_ONLY  CI above 1 but not faster under some salt
  SLOWER       CI below 1
  TIE          otherwise
"""

from pathlib import Path

import numpy as np

from llvm04.bench.timing import run_interleaved, speedup_ci


def confirm(cfg: dict, best: Path, o3: Path, seed: int) -> tuple[dict, list[dict]]:
    """Returns (result fields, raw timing records: one per salt, salt None = main)."""
    s, t = cfg["search"], cfg["timing"]
    timeout = cfg["limits"]["run_timeout_s"]
    samp = run_interleaved({"best": best, "O3": o3}, 1, s["confirm_reps"], timeout)
    sp = speedup_ci(samp["O3"], samp["best"], t["bootstrap_samples"], seed)
    raw = [{"salt": None, **samp}]
    out = {"speedup_vs_O3": sp.ratio, "ci_lo": sp.ci_lo, "ci_hi": sp.ci_hi}
    if sp.ci_lo > 1:
        ratios = []
        for salt in s["layout_salts"]:
            ls = run_interleaved({"best": best, "O3": o3}, 0, s["layout_reps"], timeout,
                                 env={"LLVM04_LAYOUT_SALT": str(salt)})
            ratios.append(float(np.median(ls["O3"]) / np.median(ls["best"])))
            raw.append({"salt": salt, **ls})
        out["layout_min_ratio"] = min(ratios)
        out["verdict"] = "WIN" if min(ratios) > 1 else "LAYOUT_ONLY"
    elif sp.ci_hi < 1:
        out["verdict"] = "SLOWER"
    else:
        out["verdict"] = "TIE"
    return out, raw
