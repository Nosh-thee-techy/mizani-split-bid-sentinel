"""Tests for the hash-chained evidence ledger (Prompt 3 / eval E11)."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from mizani.ledger import Ledger, LedgerError, logged_tool_call
from mizani.ledger.models import ApprovalPayload, LedgerActor, LedgerEntry, LedgerEvent


def _tool_entry(run_id: str = "run_test") -> LedgerEntry:
    return LedgerEntry(
        run_id=run_id,
        actor=LedgerActor.AGENT,
        event=LedgerEvent.TOOL_CALL,
        tool="search_awards",
        inputs={"buyer": "x"},
        outputs_digest="abc",
        record_ids=["r1"],
        model="test-model",
        tokens_in=1,
        tokens_out=2,
        duration_ms=3,
    )


def test_clean_chain_verifies(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    for _ in range(5):
        ledger.append(_tool_entry())
    ok, broken, msg = ledger.verify()
    assert ok is True
    assert broken is None
    assert "5 entries" in msg


def test_edit_past_row_detected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    for _ in range(5):
        ledger.append(_tool_entry())
    conn = sqlite3.connect(str(path))
    conn.execute(
        "UPDATE ledger_entries SET inputs = ? WHERE entry_id = 2",
        [json.dumps({"tampered": True})],
    )
    conn.commit()
    conn.close()
    ok, broken, msg = ledger.verify()
    assert ok is False
    assert broken == 2
    assert "entry_hash mismatch" in msg


def test_delete_row_detected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    for _ in range(4):
        ledger.append(_tool_entry())
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM ledger_entries WHERE entry_id = 2")
    conn.commit()
    conn.close()
    ok, broken, _msg = ledger.verify()
    assert ok is False
    assert broken is not None


def test_reorder_detected(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    for _ in range(3):
        ledger.append(_tool_entry())
    conn = sqlite3.connect(str(path))
    # Swap hashes between 1 and 2 to simulate reorder corruption of linkage.
    rows = conn.execute(
        "SELECT entry_id, prev_hash, entry_hash FROM ledger_entries ORDER BY entry_id"
    ).fetchall()
    conn.execute(
        "UPDATE ledger_entries SET prev_hash = ?, entry_hash = ? WHERE entry_id = 1",
        [rows[1][1], rows[1][2]],
    )
    conn.execute(
        "UPDATE ledger_entries SET prev_hash = ?, entry_hash = ? WHERE entry_id = 2",
        [rows[0][1], rows[0][2]],
    )
    conn.commit()
    conn.close()
    ok, broken, _msg = ledger.verify()
    assert ok is False
    assert broken is not None


def test_empty_approver_rejected(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    with pytest.raises((LedgerError, ValueError)):
        ledger.append(
            LedgerEntry(
                run_id="run_test",
                actor=LedgerActor.HUMAN,
                event=LedgerEvent.APPROVAL,
                approval=ApprovalPayload(approver="   ", decision="approve"),
            )
        )


def test_concurrent_appends_keep_valid_chain(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for j in range(10):
                ledger.append(_tool_entry(run_id=f"run_{i}_{j}"))
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    ok, broken, msg = ledger.verify()
    assert ok is True, msg
    assert broken is None
    assert len(ledger.list_entries()) == 40


def test_logged_tool_call_wrapper(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.sqlite")

    def work() -> dict:
        return {"record_ids": ["a1", "a2"], "ok": True}

    result = logged_tool_call(
        ledger,
        run_id="run_wrap",
        tool="demo_tool",
        inputs={"q": 1},
        fn=work,
        model="none",
    )
    assert result["ok"] is True
    entries = ledger.list_entries()
    assert len(entries) == 1
    assert entries[0].tool == "demo_tool"
    assert entries[0].record_ids == ["a1", "a2"]
    assert entries[0].outputs_digest
