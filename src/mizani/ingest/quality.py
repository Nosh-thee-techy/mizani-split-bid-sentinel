"""Data-quality report over the normalised DuckDB tables.

Produces a JSON report with row counts, missing-field rates, outlier counts,
location coverage, and filter tallies — the artefact step 2 must show before
moving on to the ledger and detectors.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from mizani.ingest.schema import connect


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str) -> Any:
    """Run a single-value SQL query."""
    return conn.execute(sql).fetchone()[0]


def _safe_rate(numerator: int, denominator: int) -> float | None:
    """Return numerator/denominator, or None when denominator is 0."""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def build_quality_report(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Compute quality metrics from the current DuckDB analysis tables.

    Inputs:
        conn: open DuckDB connection with ingest schema populated.
    Outputs:
        Nested dict suitable for JSON serialisation.
    """
    awards_total = int(_scalar(conn, "SELECT count(*) FROM awards"))
    awards_kept = int(_scalar(conn, "SELECT count(*) FROM awards WHERE NOT excluded"))
    awards_excluded = awards_total - awards_kept

    tenders_total = int(_scalar(conn, "SELECT count(*) FROM tenders"))
    tenders_open = int(_scalar(conn, "SELECT count(*) FROM tenders WHERE is_open"))
    tenders_kept = int(_scalar(conn, "SELECT count(*) FROM tenders WHERE NOT excluded"))

    items_kept = int(_scalar(conn, "SELECT count(*) FROM items WHERE NOT excluded"))
    buyers = int(_scalar(conn, "SELECT count(*) FROM buyers"))
    suppliers = int(_scalar(conn, "SELECT count(*) FROM suppliers"))
    aliases = int(_scalar(conn, "SELECT count(*) FROM supplier_aliases"))

    missing_buyer_name = int(
        _scalar(
            conn,
            "SELECT count(*) FROM awards WHERE NOT excluded AND (buyer_name IS NULL OR buyer_name = '')",
        )
    )
    missing_buyer_id = int(
        _scalar(
            conn,
            "SELECT count(*) FROM awards WHERE NOT excluded AND (buyer_id IS NULL OR buyer_id = '')",
        )
    )
    missing_location = int(
        _scalar(
            conn,
            """
            SELECT count(*) FROM awards
            WHERE NOT excluded
              AND (county IS NULL OR county = '')
              AND (region IS NULL OR region = '')
            """,
        )
    )
    missing_value = int(
        _scalar(
            conn,
            "SELECT count(*) FROM awards WHERE NOT excluded AND value_amount IS NULL",
        )
    )
    missing_supplier_key = int(
        _scalar(
            conn,
            "SELECT count(*) FROM awards WHERE NOT excluded AND supplier_key IS NULL",
        )
    )
    missing_date = int(
        _scalar(conn, "SELECT count(*) FROM awards WHERE NOT excluded AND date IS NULL")
    )
    missing_estimate = int(
        _scalar(
            conn,
            "SELECT count(*) FROM awards WHERE NOT excluded AND tender_estimate IS NULL",
        )
    )
    missing_supplier_id = int(
        _scalar(
            conn,
            """
            SELECT count(*) FROM awards
            WHERE NOT excluded AND (supplier_raw_id IS NULL OR supplier_raw_id = '')
            """,
        )
    )
    # Kenya PPRA bulk uses scheme "Bidders" (not a real org-id scheme).
    # Count missing/placeholder/non-registry schemes as invalid for the report.
    invalid_supplier_schemes = int(
        _scalar(
            conn,
            """
            SELECT count(*) FROM awards
            WHERE NOT excluded
              AND (
                supplier_id_scheme IS NULL OR supplier_id_scheme = ''
                OR lower(supplier_id_scheme) IN (
                  'unknown','n/a','na','none','null','bidders','bidder','supplier','suppliers'
                )
              )
            """,
        )
    )

    # Location coverage for the place lens (share with usable county or region).
    located = awards_kept - missing_location
    location_coverage = _safe_rate(located, awards_kept)

    # Value outliers among kept awards.
    # On small samples p01/p99 collapse to min/max, so also flag awards whose
    # value is more than 100x the median (kept in evidence; excluded later from
    # peer stats by winsorising in the price detector).
    outlier_beyond_pct = 0
    outlier_vs_median = 0
    value_stats: dict[str, Any] = {}
    if awards_kept > 0:
        row = conn.execute(
            """
            SELECT
                min(value_amount),
                approx_quantile(value_amount, 0.01),
                approx_quantile(value_amount, 0.5),
                approx_quantile(value_amount, 0.99),
                max(value_amount),
                avg(value_amount)
            FROM awards
            WHERE NOT excluded AND value_amount IS NOT NULL
            """
        ).fetchone()
        if row and row[0] is not None:
            p01, p50, p99 = row[1], row[2], row[3]
            value_stats = {
                "min": row[0],
                "p01": p01,
                "median": p50,
                "p99": p99,
                "max": row[4],
                "mean": row[5],
            }
            outlier_beyond_pct = int(
                _scalar(
                    conn,
                    f"""
                    SELECT count(*) FROM awards
                    WHERE NOT excluded
                      AND value_amount IS NOT NULL
                      AND (value_amount < {p01} OR value_amount > {p99})
                    """,
                )
            )
            if p50 and p50 > 0:
                outlier_vs_median = int(
                    _scalar(
                        conn,
                        f"""
                        SELECT count(*) FROM awards
                        WHERE NOT excluded
                          AND value_amount IS NOT NULL
                          AND value_amount > ({p50} * 100)
                        """,
                    )
                )

    currencies = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT value_currency FROM awards
            WHERE NOT excluded AND value_currency IS NOT NULL
            ORDER BY 1
            """
        ).fetchall()
    ]

    exclude_reasons = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            """
            SELECT coalesce(exclude_reason, '(null)'), count(*)
            FROM awards WHERE excluded
            GROUP BY 1 ORDER BY 2 DESC
            """
        ).fetchall()
    }

    fuzzy_merges = int(
        _scalar(
            conn,
            "SELECT count(*) FROM supplier_aliases WHERE match_method = 'fuzzy_name'",
        )
    )

    meta = {
        str(r[0]): r[1]
        for r in conn.execute("SELECT key, value FROM ingest_meta").fetchall()
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ingest_meta": meta,
        "row_counts": {
            "tenders_total": tenders_total,
            "tenders_kept": tenders_kept,
            "tenders_excluded_open": tenders_open,
            "awards_total": awards_total,
            "awards_kept": awards_kept,
            "awards_excluded": awards_excluded,
            "items_kept": items_kept,
            "buyers": buyers,
            "suppliers_canonical": suppliers,
            "supplier_aliases": aliases,
        },
        "filters": {
            "award_exclude_reasons": exclude_reasons,
        },
        "missing_field_rates": {
            "buyer_name": {
                "count": missing_buyer_name,
                "rate": _safe_rate(missing_buyer_name, awards_kept),
            },
            "buyer_id": {
                "count": missing_buyer_id,
                "rate": _safe_rate(missing_buyer_id, awards_kept),
            },
            "location": {
                "count": missing_location,
                "rate": _safe_rate(missing_location, awards_kept),
            },
            "value_amount": {
                "count": missing_value,
                "rate": _safe_rate(missing_value, awards_kept),
            },
            "tender_estimate": {
                "count": missing_estimate,
                "rate": _safe_rate(missing_estimate, awards_kept),
            },
            "supplier_key": {
                "count": missing_supplier_key,
                "rate": _safe_rate(missing_supplier_key, awards_kept),
            },
            "supplier_raw_id": {
                "count": missing_supplier_id,
                "rate": _safe_rate(missing_supplier_id, awards_kept),
            },
            "award_date": {
                "count": missing_date,
                "rate": _safe_rate(missing_date, awards_kept),
            },
        },
        # Presence = 1 - missing among kept awards (real-data checklist fields).
        "presence_rates": {
            "buyer_name": _safe_rate(awards_kept - missing_buyer_name, awards_kept),
            "buyer_region_or_county": location_coverage,
            "any_delivery_location": location_coverage,
            "tender_estimate_value": _safe_rate(
                awards_kept - missing_estimate, awards_kept
            ),
            "award_value": _safe_rate(awards_kept - missing_value, awards_kept),
            "supplier_id": _safe_rate(awards_kept - missing_supplier_id, awards_kept),
        },
        "real_data_checks": {
            "invalid_or_missing_supplier_id_schemes": {
                "count": invalid_supplier_schemes,
                "rate": _safe_rate(invalid_supplier_schemes, awards_kept),
            },
            "extreme_outliers_beyond_p01_p99": outlier_beyond_pct,
            "extreme_outliers_gt_100x_median": outlier_vs_median,
            "currencies": currencies,
            "open_tenders_excluded": tenders_open,
            "duplicates_note": "Duplicate releases dropped at load (latest OCID kept).",
            "items_kept": items_kept,
            "unit_price_possible": items_kept > 0,
            "note": (
                "Kenya PPRA registry bulk has zero tender items/tenderers/planning. "
                "When items_kept=0, use value-benchmark PRICE mode, not unit-price."
            ),
        },
        "place_lens": {
            "location_coverage": location_coverage,
            "located_awards": located if awards_kept else 0,
            "unlocated_awards": missing_location,
            "location_coverage_pct_display": (
                None if location_coverage is None else round(100.0 * location_coverage, 2)
            ),
            "note": (
                "LOCATION COVERAGE is the share of kept awards with county or region. "
                "Do not treat gaps as geographic patterns. Print this figure on every place view."
            ),
        },
        "value_distribution": {
            **value_stats,
            "extreme_outlier_count_beyond_p01_p99": outlier_beyond_pct,
            "extreme_outlier_count_gt_100x_median": outlier_vs_median,
            "top_1pct_vs_rest_note": (
                "Registry quirk: the top 1% of award values can outweigh the rest combined."
            ),
            "note": (
                "On small samples p01/p99 may equal min/max. "
                "gt_100x_median flags documented extremes that peer stats must winsorise."
            ),
        },
        "currencies": currencies,
        "duplicates": {
            "note": "Duplicate releases are dropped at load (latest per OCID kept).",
        },
        "open_tenders_excluded": tenders_open,
        "supplier_canonicalisation": {
            "fuzzy_merges": fuzzy_merges,
            "canonical_suppliers": suppliers,
            "aliases": aliases,
        },
    }


def write_quality_report(
    db_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    """Open the DB, build the report, write JSON, and return the dict.

    Inputs:
        db_path: path to mizani.duckdb.
        out_path: destination JSON path.
    Outputs:
        The report dict (also written to disk).
    """
    conn = connect(db_path)
    try:
        report = build_quality_report(conn)
    finally:
        conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
