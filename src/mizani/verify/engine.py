"""Recompute finding metrics from DuckDB (or in-memory rows) without detectors."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from mizani.finding import Finding, FindingStatus, FindingType


def verify_finding(
    finding: Finding,
    *,
    db_path: Path | None = None,
    awards: list[dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
    value_tolerance: float = 0.01,
) -> Finding:
    """Verify *finding* by reloading cited records and recomputing metrics.

    Inputs:
        finding: candidate finding with record_ids and metrics.
        db_path: DuckDB analysis database (used when awards/items not passed).
        awards/items: optional in-memory rows for unit tests.
        value_tolerance: relative tolerance for numeric metric comparison.
    Outputs:
        Finding with status VERIFIED or DROPPED and drop_reason set on failure.
    """
    result = finding.model_copy(deep=True)
    if not result.record_ids:
        result.status = FindingStatus.DROPPED
        result.drop_reason = "missing record: empty record_ids"
        return result

    if awards is None:
        if db_path is None:
            result.status = FindingStatus.DROPPED
            result.drop_reason = "missing record: no database or awards provided"
            return result
        awards = _load_awards(db_path, result.record_ids)
        items = items if items is not None else _load_items(db_path, result.record_ids)

    by_id = {str(a["record_id"]): a for a in awards}
    for rid in result.record_ids:
        if rid not in by_id and result.type != FindingType.PRICE:
            # PRICE cites item record ids
            pass

    if result.type == FindingType.PRICE:
        item_by_id = {str(i["record_id"]): i for i in (items or [])}
        for rid in result.record_ids:
            if rid not in item_by_id:
                result.status = FindingStatus.DROPPED
                result.drop_reason = f"missing record: {rid}"
                return result
        return _verify_price(result, item_by_id, value_tolerance)

    for rid in result.record_ids:
        if rid not in by_id:
            result.status = FindingStatus.DROPPED
            result.drop_reason = f"missing record: {rid}"
            return result
        row = by_id[rid]
        if result.buyer_id and row.get("buyer_id") and str(row["buyer_id"]) != result.buyer_id:
            result.status = FindingStatus.DROPPED
            result.drop_reason = (
                f"mismatched field: buyer_id expected {result.buyer_id} "
                f"actual {row.get('buyer_id')} on {rid}"
            )
            return result
        if (
            result.supplier_key
            and row.get("supplier_key")
            and str(row["supplier_key"]) != result.supplier_key
        ):
            result.status = FindingStatus.DROPPED
            result.drop_reason = (
                f"mismatched field: supplier_key expected {result.supplier_key} "
                f"actual {row.get('supplier_key')} on {rid}"
            )
            return result

    if result.type == FindingType.SPLIT:
        return _verify_split(result, by_id, value_tolerance)
    if result.type == FindingType.CONCENTRATION:
        return _verify_concentration(result, by_id, value_tolerance)
    if result.type == FindingType.GAP:
        return _verify_gap(result, by_id, value_tolerance)

    result.status = FindingStatus.VERIFIED
    return result


def _verify_split(
    finding: Finding,
    by_id: dict[str, dict[str, Any]],
    tol: float,
) -> Finding:
    """Recompute SPLIT aggregate metrics from cited awards only."""
    rows = [by_id[r] for r in finding.record_ids]
    aggregate = sum(float(r["value_amount"]) for r in rows)
    expected = float(finding.metrics.get("aggregate_value", aggregate))
    if not _close(aggregate, expected, tol):
        finding.status = FindingStatus.DROPPED
        finding.drop_reason = (
            f"metric mismatch: aggregate_value expected {expected} actual {aggregate}"
        )
        return finding
    count = len(rows)
    if int(finding.metrics.get("count", count)) != count:
        finding.status = FindingStatus.DROPPED
        finding.drop_reason = (
            f"metric mismatch: count expected {finding.metrics.get('count')} actual {count}"
        )
        return finding
    finding.status = FindingStatus.VERIFIED
    finding.drop_reason = None
    return finding


def _verify_price(
    finding: Finding,
    item_by_id: dict[str, dict[str, Any]],
    tol: float,
) -> Finding:
    """Check the cited item still has the stored unit price."""
    rid = finding.record_ids[0]
    actual = float(item_by_id[rid]["unit_value_amount"])
    expected = float(finding.metrics.get("unit_price", actual))
    if not _close(actual, expected, tol):
        finding.status = FindingStatus.DROPPED
        finding.drop_reason = (
            f"metric mismatch: unit_price expected {expected} actual {actual}"
        )
        return finding
    finding.status = FindingStatus.VERIFIED
    finding.drop_reason = None
    return finding


def _verify_concentration(
    finding: Finding,
    by_id: dict[str, dict[str, Any]],
    tol: float,
) -> Finding:
    """Recompute award share from cited records vs metric category_awards."""
    n = len(finding.record_ids)
    category_awards = int(finding.metrics.get("category_awards") or n)
    actual_share = n / category_awards if category_awards else 0.0
    expected = float(finding.metrics.get("award_share", actual_share))
    if not _close(actual_share, expected, tol):
        finding.status = FindingStatus.DROPPED
        finding.drop_reason = (
            f"metric mismatch: award_share expected {expected} actual {actual_share}"
        )
        return finding
    finding.status = FindingStatus.VERIFIED
    finding.drop_reason = None
    return finding


def _verify_gap(
    finding: Finding,
    by_id: dict[str, dict[str, Any]],
    tol: float,
) -> Finding:
    """Recompute award/estimate ratio from the cited award."""
    row = by_id[finding.record_ids[0]]
    value = float(row["value_amount"])
    estimate = float(row["tender_estimate"])
    ratio = value / estimate if estimate else 0.0
    expected = float(finding.metrics.get("award_to_estimate", ratio))
    if not _close(ratio, expected, tol):
        finding.status = FindingStatus.DROPPED
        finding.drop_reason = (
            f"metric mismatch: award_to_estimate expected {expected} actual {ratio}"
        )
        return finding
    finding.status = FindingStatus.VERIFIED
    finding.drop_reason = None
    return finding


def _close(actual: float, expected: float, tol: float) -> bool:
    """Relative closeness check with absolute floor for near-zero values."""
    scale = max(abs(expected), abs(actual), 1e-9)
    return abs(actual - expected) / scale <= tol


def _load_awards(db_path: Path, record_ids: list[str]) -> list[dict[str, Any]]:
    """Load award rows by record_id from DuckDB."""
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        placeholders = ",".join(["?"] * len(record_ids))
        rows = conn.execute(
            f"""
            SELECT record_id, buyer_id, buyer_name, supplier_key, category,
                   value_amount, tender_estimate, date
            FROM awards
            WHERE record_id IN ({placeholders})
            """,
            record_ids,
        ).fetchall()
        cols = [
            "record_id",
            "buyer_id",
            "buyer_name",
            "supplier_key",
            "category",
            "value_amount",
            "tender_estimate",
            "date",
        ]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def _load_items(db_path: Path, record_ids: list[str]) -> list[dict[str, Any]]:
    """Load item rows by record_id from DuckDB."""
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        placeholders = ",".join(["?"] * len(record_ids))
        rows = conn.execute(
            f"""
            SELECT record_id, buyer_id, supplier_key, category,
                   unit_value_amount, description_norm, unit_norm
            FROM items
            WHERE record_id IN ({placeholders})
            """,
            record_ids,
        ).fetchall()
        cols = [
            "record_id",
            "buyer_id",
            "supplier_key",
            "category",
            "unit_value_amount",
            "description_norm",
            "unit_norm",
        ]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()
