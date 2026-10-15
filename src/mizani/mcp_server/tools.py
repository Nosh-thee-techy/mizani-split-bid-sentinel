"""Domain tool implementations for mizani-mcp (no ledger writes here).

Every public function returns Pydantic models / dicts. The MCP layer and
CLI wrap calls with logged_tool_call so tools never touch the ledger.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from mizani.detect.concentration import detect_concentration
from mizani.detect.gap import detect_award_estimate_gaps
from mizani.detect.price import detect_price_outliers
from mizani.detect.split import detect_splits
from mizani.finding import Finding, FindingStatus
from mizani.verify import verify_finding


class SearchAwardsResult(BaseModel):
    """Structured search result with citation IDs and coverage note."""

    awards: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    coverage_note: str = ""


class DraftBriefResult(BaseModel):
    """Result of drafting a committee brief (always DRAFT)."""

    brief_id: str
    path: str
    status: str = "DRAFT"
    content_hash: str
    finding_ids: list[str] = Field(default_factory=list)


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


def search_awards(
    db_path: Path,
    *,
    buyer: str | None = None,
    supplier: str | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    value_min: float | None = None,
    value_max: float | None = None,
    limit: int = 50,
) -> SearchAwardsResult:
    """Query kept awards with optional filters; always returns record IDs."""
    clauses = ["NOT excluded"]
    params: list[Any] = []
    if buyer:
        clauses.append("(buyer_id = ? OR buyer_name ILIKE ?)")
        params.extend([buyer, f"%{buyer}%"])
    if supplier:
        clauses.append("(supplier_key = ? OR supplier_canonical_name ILIKE ?)")
        params.extend([supplier, f"%{supplier}%"])
    if category:
        clauses.append("category ILIKE ?")
        params.append(f"%{category}%")
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if value_min is not None:
        clauses.append("value_amount >= ?")
        params.append(value_min)
    if value_max is not None:
        clauses.append("value_amount <= ?")
        params.append(value_max)
    sql = f"""
        SELECT record_id, ocid, award_id, buyer_id, buyer_name, supplier_key,
               supplier_canonical_name, category, value_amount, value_currency,
               date, county, region, data_caveats
        FROM awards
        WHERE {' AND '.join(clauses)}
        ORDER BY date NULLS LAST
        LIMIT ?
    """
    params.append(limit)
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        cols = [
            "record_id",
            "ocid",
            "award_id",
            "buyer_id",
            "buyer_name",
            "supplier_key",
            "supplier_canonical_name",
            "category",
            "value_amount",
            "value_currency",
            "date",
            "county",
            "region",
            "data_caveats",
        ]
        awards = []
        for row in rows:
            item = dict(zip(cols, row))
            if item.get("date") is not None:
                item["date"] = str(item["date"])
            awards.append(item)
        located = sum(1 for a in awards if a.get("county") or a.get("region"))
        coverage = (located / len(awards)) if awards else None
        note = (
            f"Returned {len(awards)} awards; location coverage in result "
            f"{coverage:.0%}."
            if coverage is not None
            else "No awards matched filters."
        )
        return SearchAwardsResult(awards=awards, count=len(awards), coverage_note=note)
    finally:
        conn.close()


def _load_all_awards(db_path: Path) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT record_id, buyer_id, buyer_name, supplier_key, category,
                   value_amount, tender_estimate, date, data_caveats
            FROM awards WHERE NOT excluded
            """
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
            "data_caveats",
        ]
        out = []
        for row in rows:
            d = dict(zip(cols, row))
            if isinstance(d.get("data_caveats"), str):
                try:
                    d["data_caveats"] = json.loads(d["data_caveats"])
                except json.JSONDecodeError:
                    d["data_caveats"] = []
            out.append(d)
        return out
    finally:
        conn.close()


def _load_all_items(db_path: Path) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT record_id, buyer_id, supplier_key, category,
                   description_norm, unit_norm, unit_value_amount
            FROM items WHERE NOT excluded
            """
        ).fetchall()
        cols = [
            "record_id",
            "buyer_id",
            "supplier_key",
            "category",
            "description_norm",
            "unit_norm",
            "unit_value_amount",
        ]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def cluster_threshold_awards(
    db_path: Path,
    *,
    thresholds_doc: dict[str, Any],
    detectors_cfg: dict[str, Any],
    buyer: str | None = None,
    category: str | None = None,
) -> list[Finding]:
    """Run SPLIT detector on awards (optionally filtered)."""
    awards = _load_all_awards(db_path)
    if buyer:
        awards = [
            a
            for a in awards
            if a.get("buyer_id") == buyer or (a.get("buyer_name") or "") == buyer
        ]
    if category:
        awards = [a for a in awards if category.lower() in str(a.get("category") or "").lower()]
    return detect_splits(awards, detectors_cfg=detectors_cfg, thresholds_doc=thresholds_doc)


def benchmark_unit_price(
    db_path: Path,
    *,
    detectors_cfg: dict[str, Any],
    award_record_id: str | None = None,
) -> list[Finding]:
    """Run PRICE detector; optional focus on one item/award record id."""
    items = _load_all_items(db_path)
    return detect_price_outliers(
        items,
        detectors_cfg=detectors_cfg,
        target_record_id=award_record_id,
    )


def profile_supplier(
    db_path: Path,
    *,
    supplier_key: str,
    detectors_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Return supplier aliases/shares plus CONCENTRATION findings for that key."""
    awards = [a for a in _load_all_awards(db_path) if a.get("supplier_key") == supplier_key]
    findings = [
        f
        for f in detect_concentration(awards if awards else _load_all_awards(db_path), detectors_cfg=detectors_cfg)
        if f.supplier_key == supplier_key
    ]
    return {
        "supplier_key": supplier_key,
        "award_count": len(awards),
        "record_ids": [a["record_id"] for a in awards] or ["supplier:none"],
        "findings": [f.model_dump(mode="json") for f in findings],
    }


def verify_finding_tool(
    finding: Finding,
    *,
    db_path: Path,
) -> Finding:
    """Independent verification wrapper."""
    return verify_finding(finding, db_path=db_path)


def draft_committee_brief(
    *,
    findings: list[Finding],
    requester: str,
    out_dir: Path,
    run_id: str,
) -> DraftBriefResult:
    """Write a DRAFT brief markdown file; never releases or approves."""
    from mizani.brief.render import render_brief_markdown

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    brief_id = f"brief_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    verified = [f for f in findings if f.status == FindingStatus.VERIFIED]
    body = render_brief_markdown(
        brief_id=brief_id,
        requester=requester,
        run_id=run_id,
        findings=verified,
        status="DRAFT",
    )
    path = out_dir / f"{brief_id}.md"
    path.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    meta = {
        "brief_id": brief_id,
        "status": "DRAFT",
        "content_hash": digest,
        "path": str(path),
        "finding_ids": [f.finding_id for f in verified],
        "requester": requester,
        "run_id": run_id,
    }
    (out_dir / f"{brief_id}.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return DraftBriefResult(
        brief_id=brief_id,
        path=str(path),
        status="DRAFT",
        content_hash=digest,
        finding_ids=[f.finding_id for f in verified],
    )
