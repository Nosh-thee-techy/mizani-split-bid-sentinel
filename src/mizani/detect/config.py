"""Load detector and threshold configuration from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from *path*."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def configured_thresholds(thresholds_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Return threshold entries that have both a numeric value and legal_source.

    Null or missing values mean SPLIT must not invent numbers — callers should
    return THRESHOLDS_NOT_CONFIGURED instead of guessing.
    """
    ready: list[dict[str, Any]] = []
    for entry in thresholds_doc.get("thresholds") or []:
        if entry.get("value") is None:
            continue
        if not entry.get("legal_source"):
            continue
        ready.append(entry)
    return ready
