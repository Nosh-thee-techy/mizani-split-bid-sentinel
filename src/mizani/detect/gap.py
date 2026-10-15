"""Accountability gap: award value versus tender estimate.

A large ratio is an indicator for a human to check, not a conclusion of
wrongdoing. Each finding cites the award record ID.
"""

from __future__ import annotations

from typing import Any, Iterable

from mizani.detect.config import load_yaml
from mizani.finding import Finding, FindingStatus, FindingType
from pathlib import Path


def detect_award_estimate_gaps(
    awards: Iterable[dict[str, Any]],
    *,
    detectors_cfg: dict[str, Any] | None = None,
    detectors_path: Path | None = None,
) -> list[Finding]:
    """Flag awards whose value departs from the tender estimate beyond the ratio."""
    if detectors_cfg is None and detectors_path is not None:
        detectors_cfg = load_yaml(detectors_path)
    cfg = (detectors_cfg or {}).get("gap") or {}
    flag_ratio = float(cfg.get("award_to_estimate_flag", 1.5))

    findings: list[Finding] = []
    for award in awards:
        estimate = award.get("tender_estimate")
        value = award.get("value_amount")
        record_id = award.get("record_id")
        if estimate is None or value is None or not record_id:
            continue
        estimate_f = float(estimate)
        value_f = float(value)
        if estimate_f <= 0:
            continue
        ratio = value_f / estimate_f
        if ratio < flag_ratio and ratio > (1.0 / flag_ratio):
            continue
        findings.append(
            Finding(
                type=FindingType.GAP,
                buyer_id=award.get("buyer_id"),
                supplier_key=award.get("supplier_key"),
                category=award.get("category"),
                record_ids=[str(record_id)],
                metrics={
                    "award_value": value_f,
                    "tender_estimate": estimate_f,
                    "award_to_estimate": round(ratio, 4),
                    "flag_ratio": flag_ratio,
                },
                score=round(min(abs(math_log_ratio(ratio)) / 2.0, 1.0), 4),
                status=FindingStatus.CANDIDATE,
                benign_explanations=[
                    "scope change after tender",
                    "estimate incomplete",
                ],
                data_caveats=list(award.get("data_caveats") or []),
            )
        )
    return findings


def math_log_ratio(ratio: float) -> float:
    """Absolute log deviation of a ratio from 1.0 (local import avoided for clarity)."""
    import math

    return abs(math.log(ratio))
