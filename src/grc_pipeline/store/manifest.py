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
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    boundary_id: str
    timeframe_start: str
    timeframe_end: str
    logic_version: str
    config_hash: str
    boundary_geojson_hash: str
    herd_snapshot_hash: str
    input_data_versions: dict[str, Any]
    dq_summary: dict[str, Any]
    outputs: dict[str, Any]
    created_at: str

    def to_json(self) -> str:
        return stable_json_dumps(asdict(self))

    def snapshot_id(self) -> str:
        return sha256_text(self.to_json())


def write_manifest(path: str | Path, manifest: RunManifest) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(manifest.to_json() + "\n", encoding="utf-8")
