"""Config loading and repo-relative path resolution."""

import functools
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "default.yaml"


LOCAL_CONFIG = REPO_ROOT / "configs" / "local.yaml"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None) -> dict:
    """The config file, overlaid with configs/local.yaml if present (gitignored:
    machine- or account-specific settings such as the agent's model and prices)."""
    with open(path or DEFAULT_CONFIG) as f:
        cfg = yaml.safe_load(f)
    if LOCAL_CONFIG.exists():
        with open(LOCAL_CONFIG) as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
    return cfg


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
