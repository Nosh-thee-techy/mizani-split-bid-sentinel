"""DuckDB schema creation and row insertion for normalised OCDS tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import duckdb

from mizani.ingest.models import (
    AwardRow,
    BuyerRow,
    ItemRow,
    SupplierAliasRow,
    SupplierRow,
    TenderRow,
    model_to_dict,
)

DDL = """
CREATE TABLE IF NOT EXISTS tenders (
    ocid VARCHAR,
    tender_id VARCHAR,
    record_id VARCHAR PRIMARY KEY,
    title VARCHAR,
    description VARCHAR,
    status VARCHAR,
    procurement_method VARCHAR,
    category VARCHAR,
    value_amount DOUBLE,
    value_currency VARCHAR,
    buyer_id VARCHAR,
    buyer_name VARCHAR,
    county VARCHAR,
    region VARCHAR,
    tender_period_end DATE,
    date_published DATE,
    release_id VARCHAR,
    release_date TIMESTAMP,
    is_open BOOLEAN,
    excluded BOOLEAN,
    exclude_reason VARCHAR
);

CREATE TABLE IF NOT EXISTS awards (
    ocid VARCHAR,
    award_id VARCHAR,
    record_id VARCHAR PRIMARY KEY,
    title VARCHAR,
    status VARCHAR,
    date DATE,
    value_amount DOUBLE,
    value_currency VARCHAR,
    buyer_id VARCHAR,
    buyer_name VARCHAR,
    supplier_raw_id VARCHAR,
    supplier_raw_name VARCHAR,
    supplier_key VARCHAR,
    supplier_canonical_name VARCHAR,
    supplier_match_confidence DOUBLE,
    category VARCHAR,
    procurement_method VARCHAR,
    county VARCHAR,
    region VARCHAR,
    tender_estimate DOUBLE,
    tender_estimate_currency VARCHAR,
    supplier_id_scheme VARCHAR,
    release_id VARCHAR,
    release_date TIMESTAMP,
    excluded BOOLEAN,
    exclude_reason VARCHAR,
    data_caveats VARCHAR  -- JSON array
);


CREATE TABLE IF NOT EXISTS buyers (
    buyer_id VARCHAR PRIMARY KEY,
    name VARCHAR,
    county VARCHAR,
    region VARCHAR
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_key VARCHAR PRIMARY KEY,
    canonical_name VARCHAR,
    raw_ids VARCHAR,   -- JSON array
    raw_names VARCHAR, -- JSON array
    is_company BOOLEAN,
    notes VARCHAR
);

CREATE TABLE IF NOT EXISTS supplier_aliases (
    alias_id VARCHAR PRIMARY KEY,
    supplier_key VARCHAR,
    raw_id VARCHAR,
    raw_name VARCHAR,
    match_confidence DOUBLE,
    match_method VARCHAR
);

CREATE TABLE IF NOT EXISTS items (
    ocid VARCHAR,
    award_id VARCHAR,
    record_id VARCHAR PRIMARY KEY,
    item_id VARCHAR,
    description VARCHAR,
    description_norm VARCHAR,
    quantity DOUBLE,
    unit VARCHAR,
    unit_norm VARCHAR,
    unit_value_amount DOUBLE,
    unit_value_currency VARCHAR,
    total_value_amount DOUBLE,
    buyer_id VARCHAR,
    supplier_key VARCHAR,
    category VARCHAR,
    award_date DATE,
    excluded BOOLEAN,
    exclude_reason VARCHAR
);

CREATE TABLE IF NOT EXISTS ingest_meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);
"""


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open (or create) the analysis DuckDB file and ensure parent dirs exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create empty tables if they do not exist."""
    conn.execute(DDL)


def reset_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Drop and recreate analysis tables for a clean ingest."""
    for table in (
        "items",
        "awards",
        "tenders",
        "buyers",
        "suppliers",
        "supplier_aliases",
        "ingest_meta",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_schema(conn)


def _json_list(value: Any) -> str:
    """Encode a list field as a JSON string for DuckDB VARCHAR storage."""
    if value is None:
        return "[]"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def insert_tenders(conn: duckdb.DuckDBPyConnection, rows: Iterable[TenderRow]) -> int:
    """Insert tender rows; returns count inserted."""
    count = 0
    for row in rows:
        d = model_to_dict(row)
        conn.execute(
            """
            INSERT INTO tenders VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            [
                d["ocid"],
                d["tender_id"],
                d["record_id"],
                d["title"],
                d["description"],
                d["status"],
                d["procurement_method"],
                d["category"],
                d["value_amount"],
                d["value_currency"],
                d["buyer_id"],
                d["buyer_name"],
                d["county"],
                d["region"],
                d["tender_period_end"],
                d["date_published"],
                d["release_id"],
                d["release_date"],
                d["is_open"],
                d["excluded"],
                d["exclude_reason"],
            ],
        )
        count += 1
    return count


def insert_awards(conn: duckdb.DuckDBPyConnection, rows: Iterable[AwardRow]) -> int:
    """Insert award rows; returns count inserted."""
    count = 0
    for row in rows:
        d = model_to_dict(row)
        conn.execute(
            """
            INSERT INTO awards VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            [
                d["ocid"],
                d["award_id"],
                d["record_id"],
                d["title"],
                d["status"],
                d["date"],
                d["value_amount"],
                d["value_currency"],
                d["buyer_id"],
                d["buyer_name"],
                d["supplier_raw_id"],
                d["supplier_raw_name"],
                d["supplier_key"],
                d["supplier_canonical_name"],
                d["supplier_match_confidence"],
                d["category"],
                d["procurement_method"],
                d["county"],
                d["region"],
                d["tender_estimate"],
                d["tender_estimate_currency"],
                d["supplier_id_scheme"],
                d["release_id"],
                d["release_date"],
                d["excluded"],
                d["exclude_reason"],
                _json_list(d["data_caveats"]),
            ],
        )
        count += 1
    return count


def insert_buyers(conn: duckdb.DuckDBPyConnection, rows: Iterable[BuyerRow]) -> int:
    """Insert buyer rows (caller deduplicates by buyer_id before insert)."""
    count = 0
    for row in rows:
        d = model_to_dict(row)
        conn.execute(
            "INSERT INTO buyers (buyer_id, name, county, region) VALUES (?, ?, ?, ?)",
            [d["buyer_id"], d["name"], d["county"], d["region"]],
        )
        count += 1
    return count


def insert_suppliers(conn: duckdb.DuckDBPyConnection, rows: Iterable[SupplierRow]) -> int:
    """Insert canonical supplier rows."""
    count = 0
    for row in rows:
        d = model_to_dict(row)
        conn.execute(
            "INSERT INTO suppliers VALUES (?,?,?,?,?,?)",
            [
                d["supplier_key"],
                d["canonical_name"],
                _json_list(d["raw_ids"]),
                _json_list(d["raw_names"]),
                d["is_company"],
                d["notes"],
            ],
        )
        count += 1
    return count


def insert_aliases(conn: duckdb.DuckDBPyConnection, rows: Iterable[SupplierAliasRow]) -> int:
    """Insert supplier alias rows."""
    count = 0
    for row in rows:
        d = model_to_dict(row)
        conn.execute(
            "INSERT INTO supplier_aliases VALUES (?,?,?,?,?,?)",
            [
                d["alias_id"],
                d["supplier_key"],
                d["raw_id"],
                d["raw_name"],
                d["match_confidence"],
                d["match_method"],
            ],
        )
        count += 1
    return count


def insert_items(conn: duckdb.DuckDBPyConnection, rows: Iterable[ItemRow]) -> int:
    """Insert item rows."""
    count = 0
    for row in rows:
        d = model_to_dict(row)
        conn.execute(
            """
            INSERT INTO items VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            [
                d["ocid"],
                d["award_id"],
                d["record_id"],
                d["item_id"],
                d["description"],
                d["description_norm"],
                d["quantity"],
                d["unit"],
                d["unit_norm"],
                d["unit_value_amount"],
                d["unit_value_currency"],
                d["total_value_amount"],
                d["buyer_id"],
                d["supplier_key"],
                d["category"],
                d["award_date"],
                d["excluded"],
                d["exclude_reason"],
            ],
        )
        count += 1
    return count


def write_meta(conn: duckdb.DuckDBPyConnection, meta: dict[str, str]) -> None:
    """Store key/value ingest metadata (source, dates, stats JSON).

    Tables are reset on each ingest, so plain INSERT is sufficient.
    """
    for key, value in meta.items():
        conn.execute(
            "INSERT INTO ingest_meta (key, value) VALUES (?, ?)",
            [key, value],
        )
