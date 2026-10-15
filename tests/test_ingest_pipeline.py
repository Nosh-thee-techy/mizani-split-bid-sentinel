"""Integration test: ingest synthetic sample and check filters + quality."""

from __future__ import annotations

from pathlib import Path

import duckdb

from mizani.ingest.pipeline import run_ingest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "samples" / "synthetic_kenya_shaped.jsonl"


def test_ingest_synthetic_sample(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    # Copy sample into temp raw dir
    (raw_dir / "synthetic_kenya_shaped.jsonl").write_text(
        SAMPLE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    db_path = tmp_path / "mizani.duckdb"
    quality_out = tmp_path / "quality.json"

    result = run_ingest(
        raw_dir=raw_dir,
        db_path=db_path,
        ingest_config_path=ROOT / "config" / "ingest.yaml",
        detectors_config_path=ROOT / "config" / "detectors.yaml",
        quality_out=quality_out,
        sample_label="synthetic-sample",
    )

    stats = result["stats"]
    # One duplicate release for syn-001 should be dropped
    assert stats["duplicate_releases_dropped"] >= 1
    # Open tender syn-010 excluded
    assert stats["tenders_excluded_open"] >= 1
    assert stats["awards_excluded_open_tender"] >= 1
    # Person-like supplier excluded
    assert stats["awards_excluded_person_like"] >= 1
    assert stats["awards_kept"] >= 8

    report = result["quality_report"]
    assert report["row_counts"]["awards_kept"] == stats["awards_kept"]
    assert report["place_lens"]["location_coverage"] is not None
    assert quality_out.exists()

    conn = duckdb.connect(str(db_path))
    try:
        # No kept award should belong to the open tender ocid
        open_kept = conn.execute(
            """
            SELECT count(*) FROM awards
            WHERE NOT excluded AND ocid = 'ocds-mizani-syn-010'
            """
        ).fetchone()[0]
        assert open_kept == 0

        # Person-like award present but excluded
        person = conn.execute(
            """
            SELECT excluded, exclude_reason FROM awards
            WHERE ocid = 'ocds-mizani-syn-011'
            """
        ).fetchone()
        assert person is not None
        assert person[0] is True
        assert person[1] == "person_like_supplier"

        # Missing buyer name flagged as caveat on syn-008
        caveats = conn.execute(
            """
            SELECT data_caveats FROM awards
            WHERE ocid = 'ocds-mizani-syn-008' AND NOT excluded
            """
        ).fetchone()[0]
        assert "buyer name missing" in caveats

        # MedSupplies name variants should collapse to one canonical key
        keys = conn.execute(
            """
            SELECT DISTINCT supplier_key FROM awards
            WHERE NOT excluded
              AND ocid IN ('ocds-mizani-syn-001', 'ocds-mizani-syn-002', 'ocds-mizani-syn-003')
            """
        ).fetchall()
        assert len(keys) == 1
    finally:
        conn.close()
