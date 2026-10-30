"""Config loading and repo-relative path resolution."""

import functools
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG) as f:
        return yaml.safe_load(f)


def repo_path(cfg: dict, key: str) -> Path:
    """Resolve a `paths.<key>` entry against the repo root."""
    return REPO_ROOT / cfg["paths"][key]


@functools.cache
def sdk_path() -> str:
    """macOS SDK root, needed for system headers and linking."""
    out = subprocess.run(
        ["xcrun", "--show-sdk-path"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()
