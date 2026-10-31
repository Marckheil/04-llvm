"""Pass pipelines over the curated action space in configs/passes.yaml (D9)."""

import difflib
from pathlib import Path

import yaml

from llvm04.config import REPO_ROOT

DEFAULT_PASSES = REPO_ROOT / "configs" / "passes.yaml"
_WRAP = {
    "module": "{}",
    "cgscc": "cgscc({})",
    "function": "function({})",
    "loop": "function(loop-mssa({}))",
}


class InvalidPipeline(ValueError):
    def __init__(self, name: str, suggestions: list[str]):
        hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"unknown pass {name!r}{hint}")
        self.name = name
        self.suggestions = suggestions


def load_passes(path: str | Path | None = None) -> dict[str, str]:
    """Pass name -> kind (module, cgscc, function, loop)."""
    with open(path or DEFAULT_PASSES) as f:
        spec = yaml.safe_load(f)
    return {name: kind for kind, names in spec.items() for name in names}


def render(names: list[str], passes: dict[str, str]) -> str:
    """`opt -passes=` string for a pass sequence, each pass nested at its own level."""
    out = []
    for name in names:
        if name not in passes:
            raise InvalidPipeline(name, difflib.get_close_matches(name, passes, n=3))
        out.append(_WRAP[passes[name]].format(name))
    return ",".join(out)
