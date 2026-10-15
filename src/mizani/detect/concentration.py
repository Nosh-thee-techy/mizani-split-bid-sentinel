"""CONCENTRATION detector: supplier share and category HHI over a time window.

HHI = sum(share_value_i^2). High share alone is not enough — the category must
have enough awards so the share is meaningful. Benign readings (thin market,
framework agreement) travel with every finding.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from mizani.detect.config import load_yaml
from mizani.finding import Finding, FindingStatus, FindingType
from pathlib import Path


def detect_concentration(
    awards: Iterable[dict[str, Any]],
    *,
    detectors_cfg: dict[str, Any] | None = None,
    detectors_path: Path | None = None,
    as_of: date | None = None,
) -> list[Finding]:
    """Flag suppliers with unusual award/value share within buyer+category."""
    if detectors_cfg is None and detectors_path is not None:
        detectors_cfg = load_yaml(detectors_path)
    cfg = (detectors_cfg or {}).get("concentration") or {}
    window_days = int(cfg.get("window_days", 365))
    min_awards = int(cfg.get("min_awards_in_category", 5))
    share_flag = float(cfg.get("share_flag", 0.50))

    rows = [
        a
        for a in awards
        if a.get("date") and a.get("buyer_id") and a.get("supplier_key") and a.get("record_id")
    ]
    if not rows:
        return []

    max_date = as_of or max(_as_date(a["date"]) for a in rows)
    min_date = max_date - timedelta(days=window_days)
    windowed = [a for a in rows if min_date <= _as_date(a["date"]) <= max_date]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for award in windowed:
        groups[(str(award["buyer_id"]), str(award.get("category") or ""))].append(award)

    findings: list[Finding] = []
    for (buyer_id, category), bucket in groups.items():
        if len(bucket) < min_awards:
            continue
        by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for award in bucket:
            by_supplier[str(award["supplier_key"])].append(award)
        total_n = len(bucket)
        total_value = sum(float(a.get("value_amount") or 0.0) for a in bucket) or 1.0
        shares_value = []
        for supplier_key, s_awards in by_supplier.items():
            n_share = len(s_awards) / total_n
            v_share = sum(float(a.get("value_amount") or 0.0) for a in s_awards) / total_value
            shares_value.append(v_share)
            if n_share < share_flag and v_share < share_flag:
                continue
            caveats: list[str] = []
            if any(not a.get("buyer_name") for a in s_awards):
                caveats.append("buyer name missing on one or more records")
            findings.append(
                Finding(
                    type=FindingType.CONCENTRATION,
                    buyer_id=buyer_id,
                    supplier_key=supplier_key,
                    category=category,
                    record_ids=[str(a["record_id"]) for a in s_awards],
                    metrics={
                        "award_share": round(n_share, 4),
                        "value_share": round(v_share, 4),
                        "category_awards": total_n,
                        "window_days": window_days,
                        "share_flag": share_flag,
                    },
                    score=round(max(n_share, v_share), 4),
                    status=FindingStatus.CANDIDATE,
                    benign_explanations=[
                        "framework agreement",
                        "thin supplier market",
                        "specialised category",
                    ],
                    data_caveats=caveats,
                )
            )
        # Attach HHI onto each finding for the same category bucket
        hhi = sum(s * s for s in shares_value)
        for finding in findings:
            if finding.buyer_id == buyer_id and finding.category == category:
                finding.metrics["hhi"] = round(hhi, 4)
    return findings


def _as_date(value: Any) -> date:
    """Parse to date."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
