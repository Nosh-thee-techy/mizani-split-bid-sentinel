"""SQLite hash-chained evidence ledger.

Design choices:
- Separate file from DuckDB so analysis and audit trails never share locks.
- BEGIN IMMEDIATE serialises appenders and prevents two writers forking the tip.
- Tools never call append for themselves; use logged_tool_call as the wrapper.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TypeVar

from mizani.ledger.hashing import (
    GENESIS_PREV_HASH,
    compute_entry_hash,
    digest_outputs,
)
from mizani.ledger.models import (
    ApprovalPayload,
    LedgerActor,
    LedgerEntry,
    LedgerEvent,
)

T = TypeVar("T")


class LedgerError(Exception):
    """Raised when a ledger operation is illegal or the chain is broken."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    tool TEXT,
    inputs TEXT NOT NULL,
    outputs_digest TEXT,
    record_ids TEXT NOT NULL,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    duration_ms INTEGER,
    approval TEXT,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_run ON ledger_entries(run_id);
"""


class Ledger:
    """Append-only hash chain stored in a SQLite file.

    Inputs to the constructor:
        path: filesystem path (pathlib) to the SQLite DB; created if missing.
    """

    def __init__(self, path: Path) -> None:
        """Open or create the ledger database at *path*."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with row factory set."""
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _tip_hash(self, conn: sqlite3.Connection) -> tuple[int | None, str]:
        """Return (last entry_id, last entry_hash) or (None, genesis)."""
        row = conn.execute(
            "SELECT entry_id, entry_hash FROM ledger_entries ORDER BY entry_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None, GENESIS_PREV_HASH
        return int(row["entry_id"]), str(row["entry_hash"])

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        """Append *entry* under BEGIN IMMEDIATE and return it with ids/hashes.

        Raises LedgerError if an approval/rejection lacks a named approver.
        """
        if entry.event in {LedgerEvent.APPROVAL, LedgerEvent.REJECTION}:
            if entry.approval is None or not entry.approval.approver.strip():
                raise LedgerError("approval/rejection requires a non-empty approver name")

        with self._lock:
            with self._connect() as conn:
                # IMMEDIATE acquires a reserved lock so concurrent appends queue.
                conn.execute("BEGIN IMMEDIATE")
                _, prev_hash = self._tip_hash(conn)
                draft = entry.model_copy(deep=True)
                draft.prev_hash = prev_hash
                draft.entry_hash = None
                body = draft.to_chain_dict()
                # entry_id is assigned by SQLite; hash without it first using a
                # placeholder, then we re-hash after knowing the id is awkward.
                # Spec: entry_hash over the entry without entry_hash. We include
                # entry_id after insert by updating — cleaner: insert then set
                # hash including entry_id. Spec says entry has entry_id in the
                # payload. Flow: reserve id via insert with empty hash, then
                # update — that would break immutability. Better approach:
                # compute next id = max+1 inside the transaction, hash with it.
                next_id_row = conn.execute(
                    "SELECT COALESCE(MAX(entry_id), 0) + 1 AS next_id FROM ledger_entries"
                ).fetchone()
                next_id = int(next_id_row["next_id"])
                draft.entry_id = next_id
                body = draft.to_chain_dict()
                body.pop("entry_hash", None)
                entry_hash = compute_entry_hash(prev_hash, body)
                draft.entry_hash = entry_hash

                conn.execute(
                    """
                    INSERT INTO ledger_entries (
                        entry_id, run_id, ts, actor, event, tool, inputs,
                        outputs_digest, record_ids, model, tokens_in, tokens_out,
                        duration_ms, approval, prev_hash, entry_hash
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        draft.entry_id,
                        draft.run_id,
                        draft.ts,
                        draft.actor.value,
                        draft.event.value,
                        draft.tool,
                        json.dumps(draft.inputs, sort_keys=True),
                        draft.outputs_digest,
                        json.dumps(draft.record_ids, sort_keys=True),
                        draft.model,
                        draft.tokens_in,
                        draft.tokens_out,
                        draft.duration_ms,
                        (
                            draft.approval.model_dump_json()
                            if draft.approval is not None
                            else None
                        ),
                        draft.prev_hash,
                        draft.entry_hash,
                    ],
                )
                return draft

    def _row_to_entry(self, row: sqlite3.Row) -> LedgerEntry:
        """Deserialize a SQLite row into a LedgerEntry."""
        approval = None
        if row["approval"]:
            approval = ApprovalPayload.model_validate_json(row["approval"])
        return LedgerEntry(
            entry_id=int(row["entry_id"]),
            run_id=row["run_id"],
            ts=row["ts"],
            actor=LedgerActor(row["actor"]),
            event=LedgerEvent(row["event"]),
            tool=row["tool"],
            inputs=json.loads(row["inputs"] or "{}"),
            outputs_digest=row["outputs_digest"],
            record_ids=json.loads(row["record_ids"] or "[]"),
            model=row["model"],
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            duration_ms=row["duration_ms"],
            approval=approval,
            prev_hash=row["prev_hash"],
            entry_hash=row["entry_hash"],
        )

    def list_entries(self) -> list[LedgerEntry]:
        """Return all entries in chain order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ledger_entries ORDER BY entry_id ASC"
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def tail(self, n: int = 10) -> list[LedgerEntry]:
        """Return the last *n* entries (oldest of the window first)."""
        if n < 1:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM ledger_entries ORDER BY entry_id DESC LIMIT ?
                ) AS recent
                ORDER BY entry_id ASC
                """,
                [n],
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def verify(self) -> tuple[bool, int | None, str]:
        """Walk the chain and validate hashes.

        Outputs:
            (ok, broken_entry_id_or_None, message)
        """
        entries = self.list_entries()
        expected_prev = GENESIS_PREV_HASH
        expected_id = 1
        for entry in entries:
            assert entry.entry_id is not None
            if entry.entry_id != expected_id:
                return (
                    False,
                    entry.entry_id,
                    f"entry_id gap or reorder: expected {expected_id}, got {entry.entry_id}",
                )
            if entry.prev_hash != expected_prev:
                return (
                    False,
                    entry.entry_id,
                    f"prev_hash mismatch at entry {entry.entry_id}",
                )
            body = entry.to_chain_dict()
            body.pop("entry_hash", None)
            recomputed = compute_entry_hash(entry.prev_hash or GENESIS_PREV_HASH, body)
            if recomputed != entry.entry_hash:
                return (
                    False,
                    entry.entry_id,
                    f"entry_hash mismatch at entry {entry.entry_id}",
                )
            expected_prev = entry.entry_hash or GENESIS_PREV_HASH
            expected_id += 1
        return True, None, f"chain ok ({len(entries)} entries)"


def logged_tool_call(
    ledger: Ledger,
    *,
    run_id: str,
    tool: str,
    inputs: dict[str, Any],
    fn: Callable[[], T],
    model: str | None = None,
    record_ids: list[str] | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    actor: LedgerActor = LedgerActor.AGENT,
) -> T:
    """Run *fn*, then append a tool_call ledger entry with timing and digest.

    Tools must call their domain logic through this wrapper (or an equivalent
    that uses Ledger.append) — they must not write to the ledger themselves.
    """
    started = time.perf_counter()
    outputs = fn()
    duration_ms = int((time.perf_counter() - started) * 1000)
    # Prefer record_ids from outputs when present on a pydantic/dict result.
    ids = list(record_ids or [])
    if not ids:
        ids = _extract_record_ids(outputs)
    ledger.append(
        LedgerEntry(
            run_id=run_id,
            actor=actor,
            event=LedgerEvent.TOOL_CALL,
            tool=tool,
            inputs=inputs,
            outputs_digest=digest_outputs(_jsonable(outputs)),
            record_ids=ids,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )
    )
    return outputs


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of tool outputs to JSON-serialisable form."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


def _extract_record_ids(outputs: Any) -> list[str]:
    """Pull record_ids from a finding-like object or dict if present."""
    if hasattr(outputs, "record_ids"):
        return list(getattr(outputs, "record_ids") or [])
    if isinstance(outputs, dict) and "record_ids" in outputs:
        return list(outputs.get("record_ids") or [])
    if isinstance(outputs, list):
        ids: list[str] = []
        for item in outputs:
            ids.extend(_extract_record_ids(item))
        return ids
    return []
