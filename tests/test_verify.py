"""Verifier independence tests (Prompt 8)."""

from __future__ import annotations

from mizani.finding import Finding, FindingStatus, FindingType
from mizani.verify import verify_finding


def test_corrupt_metric_dropped() -> None:
    awards = [
        {
            "record_id": "a1",
            "buyer_id": "b1",
            "supplier_key": "s1",
            "category": "med",
            "value_amount": 100.0,
            "tender_estimate": 100.0,
            "date": "2025-01-01",
        },
        {
            "record_id": "a2",
            "buyer_id": "b1",
            "supplier_key": "s1",
            "category": "med",
            "value_amount": 100.0,
            "tender_estimate": 100.0,
            "date": "2025-01-02",
        },
    ]
    finding = Finding(
        type=FindingType.SPLIT,
        buyer_id="b1",
        supplier_key="s1",
        category="med",
        record_ids=["a1", "a2"],
        metrics={"count": 2, "aggregate_value": 9999.0},  # corrupted
        status=FindingStatus.CANDIDATE,
    )
    out = verify_finding(finding, awards=awards)
    assert out.status == FindingStatus.DROPPED
    assert out.drop_reason and "aggregate_value" in out.drop_reason


def test_missing_record_dropped() -> None:
    finding = Finding(
        type=FindingType.SPLIT,
        buyer_id="b1",
        supplier_key="s1",
        category="med",
        record_ids=["missing-id"],
        metrics={"count": 1, "aggregate_value": 10.0},
        status=FindingStatus.CANDIDATE,
    )
    out = verify_finding(finding, awards=[])
    assert out.status == FindingStatus.DROPPED
    assert out.drop_reason and "missing record" in out.drop_reason
