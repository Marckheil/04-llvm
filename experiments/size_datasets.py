"""Pick a PolyBench dataset size per benchmark so one -O3 run lands in
[timing.min_runtime_s, timing.max_runtime_s].

Starts at LARGE, steps up toward EXTRALARGE if too fast, down toward MEDIUM if
too slow. Single runs are enough: the sizes differ by ~4-30x, far above noise.
With --write, stores the result in polybench.dataset_overrides in the config.
"""

import argparse
import copy
import re
import sys

from passpilot.bench.machine import preflight_warnings
from passpilot.bench.polybench import build_bitcode, list_benchmarks
from passpilot.bench.timing import time_once
from passpilot.compiler.toolchain import Toolchain
from passpilot.config import DEFAULT_CONFIG, load_config, repo_path
from passpilot.proc import ProcTimeout

SIZES = ["SMALL_DATASET", "MEDIUM_DATASET", "LARGE_DATASET", "EXTRALARGE_DATASET"]


def measure(tc: Toolchain, cfg: dict, bench, dataset: str, timeout_s: float) -> float | None:
    """One warm run of bench at `dataset` built with default<O3>; None if it timed out."""
    cfg = copy.deepcopy(cfg)
    cfg["polybench"]["dataset_overrides"] = {bench.name: dataset}
    work = repo_path(cfg, "build_dir") / "sizing"
    bc = build_bitcode(tc, cfg, bench, "time", out=work / f"{bench.name}.{dataset}.bc")
    binary = tc.build(bc, "default<O3>", work / f"{bench.name}.{dataset}").binary
    try:
        time_once(binary, timeout_s)  # warm-up
        return time_once(binary, timeout_s)
    except ProcTimeout:
        return None


def pick(tc: Toolchain, cfg: dict, bench, lo: float, hi: float,
         timeout_s: float) -> tuple[str, float | None, str]:
    i = SIZES.index("LARGE_DATASET")
    seen: dict[int, float | None] = {}
    while True:
        seen[i] = t = measure(tc, cfg, bench, SIZES[i], timeout_s)
        too_slow = t is None or t > hi
        j = i - 1 if too_slow else i + 1 if t < lo else None
        if j is None:
            return SIZES[i], t, "ok"
        if j in seen:
            # Bouncing between two adjacent sizes: prefer meeting the floor.
            big, small = max(i, j), min(i, j)
            if seen[big] is not None:
                return SIZES[big], seen[big], "above max (next size down is below floor)"
            return SIZES[small], seen[small], "below floor (next size up timed out)"
        if j < 0 or j >= len(SIZES):
            return SIZES[i], t, "too slow at smallest size" if too_slow else "below floor at largest size"
        i = j


def write_overrides(overrides: dict[str, str]) -> None:
    """Rewrite the dataset_overrides line in place, keeping the file's comments."""
    text = DEFAULT_CONFIG.read_text()
    block = "  dataset_overrides:\n" + "".join(
        f"    {k}: {v}\n" for k, v in sorted(overrides.items()))
    new, n = re.subn(r"^  dataset_overrides:.*\n(?:    .*\n)*", block, text, flags=re.M)
    if n != 1:
        raise RuntimeError("could not find polybench.dataset_overrides in config")
    DEFAULT_CONFIG.write_text(new)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bench", nargs="*", help="only these benchmarks")
    ap.add_argument("--write", action="store_true", help="update configs/default.yaml")
    args = ap.parse_args()

    cfg = load_config()
    t = cfg["timing"]
    tc = Toolchain(cfg)
    tc.check_version()
    for w in preflight_warnings():
        print(f"WARNING: {w}", file=sys.stderr)

    default = cfg["polybench"]["time_dataset"]
    overrides = dict(cfg["polybench"]["dataset_overrides"])
    timeout_s = 3 * t["max_runtime_s"]
    for bench in list_benchmarks(cfg):
        if args.bench and bench.name not in args.bench:
            continue
        size, secs, status = pick(tc, cfg, bench, t["min_runtime_s"], t["max_runtime_s"],
                                  timeout_s)
        shown = f"{secs:7.3f}s" if secs is not None else f">{timeout_s:.0f}s".rjust(8)
        print(f"{bench.name:<16} {size:<20} {shown}  {status}", flush=True)
        if size == default:
            overrides.pop(bench.name, None)
        else:
            overrides[bench.name] = size

    if args.write:
        write_overrides(overrides)
        print(f"\nwrote {len(overrides)} overrides to {DEFAULT_CONFIG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
