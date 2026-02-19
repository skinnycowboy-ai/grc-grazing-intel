# src/grc_pipeline/runtime.py
from __future__ import annotations

import os
import platform
import subprocess
from importlib import metadata
from typing import Any


def _safe_run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        return out or None
    except Exception:
        return None


def get_git_commit() -> str:
    # Allow CI/build systems to inject this deterministically.
    for k in ("GIT_SHA", "GITHUB_SHA", "COMMIT_SHA"):
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return _safe_run(["git", "rev-parse", "HEAD"]) or "unknown"


def get_package_version(dist_name: str = "grc-grazing-intel") -> str:
    try:
        return metadata.version(dist_name)
    except Exception:
        return "unknown"


def collect_code_metadata() -> dict[str, Any]:
    return {
        "git_commit": get_git_commit(),
        "package_version": get_package_version(),
        "python": platform.python_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }
