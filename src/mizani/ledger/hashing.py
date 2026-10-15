"""Canonical JSON and SHA-256 helpers for the ledger hash chain.

Why canonical JSON: any key-order or whitespace difference would change the
hash and break verification even when the semantic content is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Genesis previous-hash: 64 zero hex chars (256-bit empty predecessor).
GENESIS_PREV_HASH = "0" * 64


def canonical_json(value: Any) -> str:
    """Serialize *value* as sorted-key, compact UTF-8 JSON.

    Inputs:
        value: Any JSON-serialisable structure.
    Outputs:
        Canonical JSON string (no trailing newline).
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """Return the hex SHA-256 digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_outputs(outputs: Any) -> str:
    """Hash tool outputs for storage (full payloads stay out of the chain body)."""
    return sha256_hex(canonical_json(outputs))


def compute_entry_hash(prev_hash: str, entry_without_hash: dict[str, Any]) -> str:
    """Compute entry_hash = sha256(prev_hash + canonical JSON without entry_hash).

    The hash binds each row to its predecessor so editing, deleting, or
    reordering any past entry breaks every subsequent hash check.
    """
    payload = {k: v for k, v in entry_without_hash.items() if k != "entry_hash"}
    return sha256_hex(prev_hash + canonical_json(payload))
