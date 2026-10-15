"""Tests for verified fetch gate and real-data value benchmark mode (Prompt 5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mizani.detect.price import detect_value_benchmarks
from mizani.finding import FindingStatus
from mizani.ingest.fetch import FetchError, fetch_year, get_verified_source


def test_fetch_refuses_unverified(tmp_path: Path) -> None:
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        yaml.dump(
            {
                "sources": [
                    {
                        "id": "x",
                        "verified": False,
                        "download_pattern": "https://example.invalid/{file}",
                        "files": ["2026.jsonl.gz"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FetchError, match="not verified"):
        get_verified_source(sources)


def test_fetch_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        yaml.dump(
            {
                "sources": [
                    {
                        "id": "x",
                        "verified": True,
                        "download_pattern": "https://example.invalid/{file}",
                        "files": ["2026.jsonl.gz"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "real"
    out.mkdir()
    (out / "2026.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(FetchError, match="overwrite"):
        fetch_year(year=2026, sources_path=sources, raw_real_dir=out)


def test_value_benchmark_labels_not_unit_price() -> None:
    awards = []
    for i in range(10):
        awards.append(
            {
                "record_id": f"a{i}",
                "buyer_id": "b",
                "supplier_key": f"s{i}",
                "category": "goods",
                "procurement_method": "open",
                "value_amount": 100_000.0,
            }
        )
    awards.append(
        {
            "record_id": "outlier",
            "buyer_id": "b",
            "supplier_key": "sx",
            "category": "goods",
            "procurement_method": "open",
            "value_amount": 5_000_000.0,
        }
    )
    found = detect_value_benchmarks(awards, detectors_cfg={"price": {"min_peers": 8, "z_cutoff": 3.5}})
    hits = [f for f in found if f.record_ids == ["outlier"] and f.status == FindingStatus.CANDIDATE]
    assert hits
    assert hits[0].metrics["benchmark_mode"] == "value_benchmark"
    assert hits[0].metrics["benchmark_label"] == "value benchmark within category"
    assert "unit price" not in hits[0].metrics["benchmark_label"].lower()
