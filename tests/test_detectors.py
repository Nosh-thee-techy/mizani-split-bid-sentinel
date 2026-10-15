"""Unit tests for deterministic detectors (Prompt 7)."""

from __future__ import annotations

import pytest
from datetime import date, timedelta

from mizani.detect.price import detect_price_outliers
from mizani.detect.split import detect_splits
from mizani.finding import Finding, FindingStatus, FindingType

# Fixture thresholds — synthetic, labelled for tests only (not legal claims).
FIXTURE_THRESHOLDS = {
    "thresholds": [
        {
            "id": "synthetic_rfq",
            "value": 500_000,
            "currency": "KES",
            "procurement_method": "limited",
            "legal_source": "SYNTHETIC FIXTURE — not a legal threshold",
            "description": "synthetic",
        }
    ]
}

DETECTORS = {
    "split": {
        "window_days": 30,
        "tolerance": 0.10,
        "min_awards": 2,
        "n_max": 10,
        "weights": {"n": 0.25, "distance": 0.25, "span": 0.25, "aggregate": 0.25},
    },
    "price": {
        "min_peers": 8,
        "z_cutoff": 3.5,
        "winsor_low_pct": 1.0,
        "winsor_high_pct": 99.0,
    },
    "concentration": {
        "window_days": 365,
        "min_awards_in_category": 5,
        "share_flag": 0.5,
    },
    "gap": {"award_to_estimate_flag": 1.5},
}


def test_finding_rejects_empty_record_ids() -> None:
    with pytest.raises(ValueError):
        Finding(type=FindingType.SPLIT, record_ids=[])


def test_null_thresholds_not_configured() -> None:
    awards = [
        {
            "record_id": "r1",
            "buyer_id": "b1",
            "supplier_key": "s1",
            "category": "goods",
            "value_amount": 480_000,
            "date": date(2025, 1, 1),
        }
    ]
    findings = detect_splits(
        awards,
        detectors_cfg=DETECTORS,
        thresholds_doc={"thresholds": [{"id": "x", "value": None, "legal_source": None}]},
    )
    assert findings[0].status == FindingStatus.THRESHOLDS_NOT_CONFIGURED


def test_injected_split_found() -> None:
    base = date(2025, 1, 1)
    awards = []
    for i in range(3):
        awards.append(
            {
                "record_id": f"split-{i}",
                "buyer_id": "buyer-a",
                "buyer_name": "Buyer A",
                "supplier_key": "sup-1",
                "category": "medical",
                "value_amount": 480_000,  # within 10% below 500k
                "date": base + timedelta(days=i * 3),
            }
        )
    findings = detect_splits(awards, detectors_cfg=DETECTORS, thresholds_doc=FIXTURE_THRESHOLDS)
    splits = [f for f in findings if f.status == FindingStatus.CANDIDATE]
    assert splits
    ids = set(splits[0].record_ids)
    assert {"split-0", "split-1", "split-2"} <= ids


def test_legitimate_above_threshold_not_flagged() -> None:
    base = date(2025, 1, 1)
    awards = [
        {
            "record_id": f"ok-{i}",
            "buyer_id": "buyer-a",
            "supplier_key": "sup-1",
            "category": "medical",
            "value_amount": 900_000,  # well above threshold
            "date": base + timedelta(days=i),
        }
        for i in range(3)
    ]
    findings = detect_splits(awards, detectors_cfg=DETECTORS, thresholds_doc=FIXTURE_THRESHOLDS)
    assert [f for f in findings if f.status == FindingStatus.CANDIDATE] == []


def test_price_outlier_and_insufficient_peers() -> None:
    peers = []
    for i in range(10):
        peers.append(
            {
                "record_id": f"peer-{i}",
                "description_norm": "surgical latex gloves size m",
                "unit_norm": "box",
                "unit_value_amount": 480.0 + (i % 3),
                "buyer_id": f"b{i}",
                "supplier_key": f"s{i}",
                "category": "medical",
            }
        )
    outlier = {
        "record_id": "outlier-1",
        "description_norm": "surgical latex gloves size m",
        "unit_norm": "box",
        "unit_value_amount": 2500.0,
        "buyer_id": "bX",
        "supplier_key": "sX",
        "category": "medical",
    }
    found = detect_price_outliers(peers + [outlier], detectors_cfg=DETECTORS)
    assert any(f.record_ids == ["outlier-1"] and f.status == FindingStatus.CANDIDATE for f in found)

    thin = detect_price_outliers(peers[:3] + [outlier], detectors_cfg=DETECTORS)
    assert any(f.status == FindingStatus.INSUFFICIENT_PEERS for f in thin)


def test_winsorise_protects_benchmark() -> None:
    peers = [
        {
            "record_id": f"p-{i}",
            "description_norm": "item x",
            "unit_norm": "unit",
            "unit_value_amount": 100.0,
            "buyer_id": "b",
            "supplier_key": "s",
            "category": "c",
        }
        for i in range(12)
    ]
    # Documented extreme in the peer set
    peers[0]["unit_value_amount"] = 1_000_000.0
    peers.append(
        {
            "record_id": "mild-high",
            "description_norm": "item x",
            "unit_norm": "unit",
            "unit_value_amount": 130.0,
            "buyer_id": "b",
            "supplier_key": "s",
            "category": "c",
        }
    )
    found = detect_price_outliers(peers, detectors_cfg=DETECTORS, target_record_id="mild-high")
    # Mild deviation should not be flagged once the extreme is winsorised
    assert found == [] or all(f.record_ids != ["mild-high"] for f in found)
