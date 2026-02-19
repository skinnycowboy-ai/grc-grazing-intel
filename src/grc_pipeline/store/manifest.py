# src/grc_pipeline/store/manifest.py
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def stable_json_dumps(obj: Any) -> str:
    # Canonical JSON for hashing / snapshot identity.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class RunManifest:
    """
    Immutable provenance record for a single pipeline "decision" run.

    Snapshot identity MUST be stable across retries/backfills.
    Therefore snapshot_id() excludes volatile fields like created_at.
    """

    schema_version: int
    run_type: str  # e.g. "compute_recommendation"
    run_id: str  # deterministic from idempotency_key
    created_at: str  # when this manifest was first written

    code: dict[str, Any]  # git_commit, package_version, python, platform...
    idempotency_key: dict[str, Any]  # boundary/herd/date + logic_version + config_hash

    inputs: dict[str, Any]  # full input snapshot values used for the decision
    dq_summary: dict[str, Any]
    outputs: dict[str, Any]  # computed outputs (no DB row ids required)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return stable_json_dumps(self.to_dict())

    def snapshot_material(self) -> dict[str, Any]:
        """
        The minimal set of fields that define the *meaning* of the run.
        Excludes timestamps to keep snapshot identity stable across retries.
        """
        code_version = {
            "git_commit": (self.code or {}).get("git_commit", "unknown"),
            "package_version": (self.code or {}).get("package_version", "unknown"),
        }
        return {
            "schema_version": self.schema_version,
            "run_type": self.run_type,
            "run_id": self.run_id,
            "code_version": code_version,
            "idempotency_key": self.idempotency_key,
            "inputs": self.inputs,
            "dq_summary": self.dq_summary,
            "outputs": self.outputs,
        }

    def snapshot_id(self) -> str:
        return sha256_text(stable_json_dumps(self.snapshot_material()))


def read_manifest(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


def write_manifest_if_missing(path: str | Path, manifest: RunManifest) -> None:
    """
    Idempotent write:
    - if file exists, never overwrite (immutability)
    - otherwise write it atomically
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        return

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(manifest.to_json() + "\n", encoding="utf-8")
    tmp.replace(p)
