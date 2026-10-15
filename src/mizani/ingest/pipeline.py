"""End-to-end OCDS ingest: load → filter → canonicalise → DuckDB → stats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from mizani.ingest.canonicalise import SupplierCanonicaliser
from mizani.ingest.loader import load_latest_releases, release_to_rows
from mizani.ingest.models import AwardRow, BuyerRow, IngestStats, ItemRow, TenderRow
from mizani.ingest.quality import write_quality_report
from mizani.ingest.schema import (
    connect,
    insert_aliases,
    insert_awards,
    insert_buyers,
    insert_items,
    insert_suppliers,
    insert_tenders,
    reset_tables,
    write_meta,
)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML config file into a dict."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def run_ingest(
    *,
    raw_dir: Path,
    db_path: Path,
    ingest_config_path: Path,
    detectors_config_path: Path | None = None,
    quality_out: Path | None = None,
    sample_label: str | None = None,
) -> dict[str, Any]:
    """Run a full ingest from raw OCDS files into DuckDB.

    Inputs:
        raw_dir: directory of .json / .jsonl OCDS packages.
        db_path: output DuckDB path (replaced on each run).
        ingest_config_path: config/ingest.yaml.
        detectors_config_path: optional; used for fuzzy_threshold.
        quality_out: optional path for the data-quality JSON report.
        sample_label: optional label stored in meta (e.g. "synthetic-sample").
    Outputs:
        Dict with stats, paths, and the quality report if requested.
    """
    ingest_cfg = load_yaml(ingest_config_path)
    filters_cfg = ingest_cfg.get("filters") or {}
    personal_cfg = ingest_cfg.get("personal_data") or {}
    source_cfg = ingest_cfg.get("source") or {}

    fuzzy_threshold = 92.0
    name_min_length = 3
    if detectors_config_path and detectors_config_path.exists():
        det = load_yaml(detectors_config_path)
        sc = det.get("supplier_canonicalisation") or {}
        fuzzy_threshold = float(sc.get("fuzzy_threshold", fuzzy_threshold))
        name_min_length = int(sc.get("name_min_length", name_min_length))

    stats = IngestStats()
    releases = load_latest_releases(raw_dir, stats)

    exclude_open = list(filters_cfg.get("exclude_open_tender_statuses") or [])
    company_markers = list(personal_cfg.get("company_markers") or [])
    person_max = int(personal_cfg.get("person_name_max_tokens", 3))
    exclude_persons = bool(personal_cfg.get("exclude_person_like_suppliers", True))
    keep_unknown = bool(filters_cfg.get("keep_awarded_when_tender_status_unknown", True))

    tenders: list[TenderRow] = []
    awards: list[AwardRow] = []
    items: list[ItemRow] = []
    buyers_by_id: dict[str, BuyerRow] = {}

    canonicaliser = SupplierCanonicaliser(
        fuzzy_threshold=fuzzy_threshold,
        name_min_length=name_min_length,
    )

    # First pass: observe all non-excluded suppliers so fuzzy merges see the full set.
    pending_awards: list[AwardRow] = []
    pending_items: list[ItemRow] = []

    for release in releases.values():
        tender_row, award_rows, item_rows, buyer_row = release_to_rows(
            release,
            exclude_open_statuses=exclude_open,
            company_markers=company_markers,
            person_name_max_tokens=person_max,
            exclude_person_like=exclude_persons,
            keep_awarded_when_unknown=keep_unknown,
            stats=stats,
        )
        if tender_row:
            tenders.append(tender_row)
        if buyer_row and buyer_row.buyer_id:
            # Prefer rows that have a name / location when merging duplicates.
            existing = buyers_by_id.get(buyer_row.buyer_id)
            if existing is None:
                buyers_by_id[buyer_row.buyer_id] = buyer_row
            else:
                buyers_by_id[buyer_row.buyer_id] = BuyerRow(
                    buyer_id=buyer_row.buyer_id,
                    name=buyer_row.name or existing.name,
                    county=buyer_row.county or existing.county,
                    region=buyer_row.region or existing.region,
                )
        for award in award_rows:
            if not award.excluded and (award.supplier_raw_id or award.supplier_raw_name):
                canonicaliser.observe(award.supplier_raw_id, award.supplier_raw_name)
            pending_awards.append(award)
        pending_items.extend(item_rows)

    # Second pass: stamp canonical keys onto awards and items.
    for award in pending_awards:
        if award.excluded:
            awards.append(award)
            continue
        key, confidence, cname = canonicaliser.resolve(
            award.supplier_raw_id,
            award.supplier_raw_name,
        )
        if key is None and (award.supplier_raw_id or award.supplier_raw_name):
            # Observe late if somehow missed (should be rare).
            key = canonicaliser.observe(award.supplier_raw_id, award.supplier_raw_name)
            key, confidence, cname = canonicaliser.resolve(
                award.supplier_raw_id,
                award.supplier_raw_name,
            )
        award.supplier_key = key
        award.supplier_match_confidence = confidence
        award.supplier_canonical_name = cname
        if key is None:
            award.data_caveats = list(award.data_caveats) + ["supplier canonicalisation failed"]
            stats.add_note(
                f"Could not canonicalise supplier on {award.record_id}; kept with caveat"
            )
        awards.append(award)

    # Attach supplier_key to items via award_id + ocid
    award_key_by_pair = {
        (a.ocid, a.award_id): a.supplier_key for a in awards if not a.excluded
    }
    for item in pending_items:
        item.supplier_key = award_key_by_pair.get((item.ocid, item.award_id))
        items.append(item)

    canon = canonicaliser.result()
    stats.buyers = len(buyers_by_id)
    stats.suppliers_canonical = len(canon.suppliers)
    stats.supplier_aliases = len(canon.aliases)

    conn = connect(db_path)
    try:
        reset_tables(conn)
        insert_tenders(conn, tenders)
        insert_awards(conn, awards)
        insert_buyers(conn, buyers_by_id.values())
        insert_suppliers(conn, canon.suppliers)
        insert_aliases(conn, canon.aliases)
        insert_items(conn, items)
        write_meta(
            conn,
            {
                "source_name": str(source_cfg.get("name") or ""),
                "portal_url": str(source_cfg.get("portal_url") or ""),
                "download_date": str(source_cfg.get("download_date") or "not-set"),
                "raw_dir": str(raw_dir),
                "sample_label": sample_label or "",
                "stats_json": json.dumps(stats.model_dump(mode="json"), sort_keys=True),
            },
        )
    finally:
        conn.close()

    result: dict[str, Any] = {
        "db_path": str(db_path),
        "raw_dir": str(raw_dir),
        "stats": stats.model_dump(mode="json"),
    }

    if quality_out is not None:
        report = write_quality_report(db_path, quality_out)
        result["quality_report_path"] = str(quality_out)
        result["quality_report"] = report

    return result
