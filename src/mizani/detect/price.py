"""PRICE / value detectors: robust peer benchmarks using log-MAD z-scores.

  z = 0.6745 * (ln(p) - median(ln(p_peers))) / MAD(ln(p_peers))

Two modes:
- **unit_price** (synthetic / when item+unit data exist): peer by description+unit.
- **value_benchmark** (real Kenya data): peer by main category + procurement method
  on award *value*, because the registry reports zero tender items/units.

Findings from value mode are always labelled "value benchmark within category"
and must never be described as unit-price outliers.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any, Iterable, Literal

from mizani.detect.config import load_yaml
from mizani.finding import Finding, FindingStatus, FindingType
from pathlib import Path

BenchmarkMode = Literal["unit_price", "value_benchmark"]


def _caveats_list(raw: Any) -> list[str]:
    """Normalise data_caveats from dict rows (list or JSON string from DuckDB)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            return [text]
        return [text]
    return [str(raw)]


def detect_price_outliers(
    items: Iterable[dict[str, Any]],
    *,
    detectors_cfg: dict[str, Any] | None = None,
    detectors_path: Path | None = None,
    target_record_id: str | None = None,
) -> list[Finding]:
    """Flag unit prices far from the peer distribution for the same item/unit.

    Synthetic / item-rich data only. Real Kenya PPRA bulk has no tender items —
    use detect_value_benchmarks() instead.

    Inputs:
        items: dicts with description_norm, unit_norm, unit_value_amount,
            record_id, optional award/buyer/supplier fields.
        target_record_id: if set, only evaluate that item (still needs peers).
    Outputs:
        PRICE findings or INSUFFICIENT_PEERS findings citing the target record.
        Metrics include benchmark_mode=unit_price.
    """
    if detectors_cfg is None and detectors_path is not None:
        detectors_cfg = load_yaml(detectors_path)
    cfg = (detectors_cfg or {}).get("price") or {}
    min_peers = int(cfg.get("min_peers", 8))
    z_cutoff = float(cfg.get("z_cutoff", 3.5))
    low_pct = float(cfg.get("winsor_low_pct", 1.0))
    high_pct = float(cfg.get("winsor_high_pct", 99.0))

    usable = [
        i
        for i in items
        if i.get("unit_value_amount")
        and float(i["unit_value_amount"]) > 0
        and i.get("description_norm")
        and i.get("unit_norm")
        and i.get("record_id")
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in usable:
        groups[(str(item["description_norm"]), str(item["unit_norm"]))].append(item)

    findings: list[Finding] = []
    for (desc, unit), peers in groups.items():
        targets = peers
        if target_record_id:
            targets = [p for p in peers if str(p["record_id"]) == target_record_id]
            if not targets:
                continue
        for t in targets:
            # Peer stats must exclude the target so one extreme cannot hide itself.
            others = [p for p in peers if str(p["record_id"]) != str(t["record_id"])]
            if len(others) < min_peers:
                findings.append(
                    Finding(
                        type=FindingType.PRICE,
                        buyer_id=t.get("buyer_id"),
                        supplier_key=t.get("supplier_key"),
                        category=t.get("category") or desc,
                        record_ids=[str(t["record_id"])],
                        metrics={
                            "peer_count": len(others),
                            "min_peers": min_peers,
                            "description_norm": desc,
                            "unit_norm": unit,
                        },
                        status=FindingStatus.INSUFFICIENT_PEERS,
                        benign_explanations=["thin peer market"],
                        data_caveats=["insufficient peers for robust benchmark"],
                    )
                )
                continue

            log_prices = [math.log(float(p["unit_value_amount"])) for p in others]
            winsorised = _winsorise(log_prices, low_pct, high_pct)
            median = _median(winsorised)
            mad = _mad(winsorised, median)
            lp = math.log(float(t["unit_value_amount"]))
            # If peers have zero MAD (identical after winsorise), flag only large
            # relative departures (default: outside 2x / 0.5x) so mild noise after
            # an extreme peer is winsorised away is not treated as an outlier.
            if mad == 0:
                if abs(lp - median) < 1e-12:
                    continue
                rel = math.exp(lp) / math.exp(median)
                if 0.5 <= rel <= 2.0:
                    continue
                z = 10.0 if lp > median else -10.0
            else:
                z = 0.6745 * (lp - median) / mad
            if abs(z) < z_cutoff:
                continue
            findings.append(
                Finding(
                    type=FindingType.PRICE,
                    buyer_id=t.get("buyer_id"),
                    supplier_key=t.get("supplier_key"),
                    category=t.get("category") or desc,
                    record_ids=[str(t["record_id"])],
                    metrics={
                        "benchmark_mode": "unit_price",
                        "benchmark_label": "unit price",
                        "unit_price": float(t["unit_value_amount"]),
                        "peer_count": len(others),
                        "log_median": round(median, 6),
                        "log_mad": round(mad, 6),
                        "robust_z": round(z, 4),
                        "z_cutoff": z_cutoff,
                        "description_norm": desc,
                        "unit_norm": unit,
                        "winsor_low_pct": low_pct,
                        "winsor_high_pct": high_pct,
                    },
                    score=round(min(abs(z) / z_cutoff, 5.0) / 5.0, 4),
                    status=FindingStatus.CANDIDATE,
                    benign_explanations=[
                        "emergency purchase",
                        "different grade/spec not captured in description",
                    ],
                    data_caveats=_caveats_list(t.get("data_caveats")),
                )
            )
    return findings


def detect_value_benchmarks(
    awards: Iterable[dict[str, Any]],
    *,
    detectors_cfg: dict[str, Any] | None = None,
    detectors_path: Path | None = None,
    target_record_id: str | None = None,
) -> list[Finding]:
    """Flag award values far from peers in the same category + procurement method.

    Real-data mode for Kenya PPRA (no item/unit fields). Every finding is
    labelled "value benchmark within category" — never "unit price".
    """
    if detectors_cfg is None and detectors_path is not None:
        detectors_cfg = load_yaml(detectors_path)
    cfg = (detectors_cfg or {}).get("price") or {}
    min_peers = int(cfg.get("min_peers", 8))
    z_cutoff = float(cfg.get("z_cutoff", 3.5))
    low_pct = float(cfg.get("winsor_low_pct", 1.0))
    high_pct = float(cfg.get("winsor_high_pct", 99.0))

    usable = [
        a
        for a in awards
        if a.get("value_amount")
        and float(a["value_amount"]) > 0
        and a.get("record_id")
        and a.get("category")
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for award in usable:
        key = (
            str(award.get("category") or ""),
            str(award.get("procurement_method") or "unknown"),
        )
        groups[key].append(award)

    findings: list[Finding] = []
    for (category, method), peers in groups.items():
        targets = peers
        if target_record_id:
            targets = [p for p in peers if str(p["record_id"]) == target_record_id]
            if not targets:
                continue
        for t in targets:
            others = [p for p in peers if str(p["record_id"]) != str(t["record_id"])]
            label = "value benchmark within category"
            if len(others) < min_peers:
                findings.append(
                    Finding(
                        type=FindingType.PRICE,
                        buyer_id=t.get("buyer_id"),
                        supplier_key=t.get("supplier_key"),
                        category=category,
                        record_ids=[str(t["record_id"])],
                        metrics={
                            "benchmark_mode": "value_benchmark",
                            "benchmark_label": label,
                            "peer_count": len(others),
                            "min_peers": min_peers,
                            "procurement_method": method,
                        },
                        status=FindingStatus.INSUFFICIENT_PEERS,
                        benign_explanations=["thin peer market within category/method"],
                        data_caveats=[
                            "insufficient peers for value benchmark",
                            "not a unit-price finding — real data has no item units",
                        ],
                    )
                )
                continue

            log_vals = [math.log(float(p["value_amount"])) for p in others]
            winsorised = _winsorise(log_vals, low_pct, high_pct)
            median = _median(winsorised)
            mad = _mad(winsorised, median)
            lv = math.log(float(t["value_amount"]))
            if mad == 0:
                if abs(lv - median) < 1e-12:
                    continue
                rel = math.exp(lv) / math.exp(median)
                if 0.5 <= rel <= 2.0:
                    continue
                z = 10.0 if lv > median else -10.0
            else:
                z = 0.6745 * (lv - median) / mad
            if abs(z) < z_cutoff:
                continue
            findings.append(
                Finding(
                    type=FindingType.PRICE,
                    buyer_id=t.get("buyer_id"),
                    supplier_key=t.get("supplier_key"),
                    category=category,
                    record_ids=[str(t["record_id"])],
                    metrics={
                        "benchmark_mode": "value_benchmark",
                        "benchmark_label": label,
                        "award_value": float(t["value_amount"]),
                        "peer_count": len(others),
                        "log_median": round(median, 6),
                        "log_mad": round(mad, 6),
                        "robust_z": round(z, 4),
                        "z_cutoff": z_cutoff,
                        "procurement_method": method,
                        "winsor_low_pct": low_pct,
                        "winsor_high_pct": high_pct,
                    },
                    score=round(min(abs(z) / z_cutoff, 5.0) / 5.0, 4),
                    status=FindingStatus.CANDIDATE,
                    benign_explanations=[
                        "emergency purchase",
                        "larger scope than peer awards in the same category",
                        "framework / multi-year package",
                    ],
                    data_caveats=_caveats_list(t.get("data_caveats"))
                    + ["not a unit-price finding — real data has no item units"],
                )
            )
    return findings


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear percentile of a sorted list (pct in 0..100)."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def _winsorise(values: list[float], low_pct: float, high_pct: float) -> list[float]:
    """Clamp extremes so one documented outlier cannot dominate MAD."""
    ordered = sorted(values)
    lo = _percentile(ordered, low_pct)
    hi = _percentile(ordered, high_pct)
    return [min(max(v, lo), hi) for v in values]


def _median(values: list[float]) -> float:
    """Median of a non-empty list."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _mad(values: list[float], median: float) -> float:
    """Median absolute deviation from *median*."""
    return _median([abs(v - median) for v in values])
