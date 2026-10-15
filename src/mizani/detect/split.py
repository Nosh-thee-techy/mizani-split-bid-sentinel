"""SPLIT detector: near-threshold award clusters in a sliding time window.

Formula (equal weights by default):
  S = w1*(n/n_max) + w2*(1 - d_bar/tau) + w3*(1 - span/W) + w4*min(sum(v)/theta, 5)/5

Why: count, closeness to the threshold, temporal tightness, and how far the
aggregate exceeds the threshold are independent signals that a committee can
inspect; the model never computes S itself.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from mizani.detect.config import configured_thresholds, load_yaml
from mizani.finding import Finding, FindingStatus, FindingType
from pathlib import Path


def detect_splits(
    awards: Iterable[dict[str, Any]],
    *,
    detectors_cfg: dict[str, Any] | None = None,
    thresholds_doc: dict[str, Any] | None = None,
    thresholds_path: Path | None = None,
    detectors_path: Path | None = None,
) -> list[Finding]:
    """Find near-threshold award clusters per buyer/category/supplier.

    Inputs:
        awards: iterable of award dicts with buyer_id, supplier_key, category,
            value_amount, date (date|str), record_id, optional data_caveats.
        detectors_cfg / thresholds_doc: in-memory config (preferred in tests).
        *_path: optional YAML paths when docs are not passed.
    Outputs:
        Finding list. If no thresholds are configured, a single finding with
        status THRESHOLDS_NOT_CONFIGURED and record_ids from the input sample.
    """
    award_list = [a for a in awards if a.get("value_amount") is not None and a.get("date")]
    if detectors_cfg is None and detectors_path is not None:
        detectors_cfg = load_yaml(detectors_path)
    if thresholds_doc is None and thresholds_path is not None:
        thresholds_doc = load_yaml(thresholds_path)
    detectors_cfg = detectors_cfg or {}
    thresholds_doc = thresholds_doc or {"thresholds": []}

    ready = configured_thresholds(thresholds_doc)
    if not ready:
        sample_ids = [str(a["record_id"]) for a in award_list[:5] if a.get("record_id")]
        if not sample_ids:
            sample_ids = ["config:thresholds-missing"]
        return [
            Finding(
                type=FindingType.SPLIT,
                record_ids=sample_ids,
                status=FindingStatus.THRESHOLDS_NOT_CONFIGURED,
                metrics={"reason": "no threshold with value and legal_source"},
                benign_explanations=[],
                data_caveats=["SPLIT disabled until thresholds.yaml is filled from regulations"],
                score=None,
            )
        ]

    split_cfg = detectors_cfg.get("split") or {}
    window_days = int(split_cfg.get("window_days", 30))
    tolerance = float(split_cfg.get("tolerance", 0.10))
    min_awards = int(split_cfg.get("min_awards", 2))
    n_max = float(split_cfg.get("n_max", 10))
    weights = split_cfg.get("weights") or {
        "n": 0.25,
        "distance": 0.25,
        "span": 0.25,
        "aggregate": 0.25,
    }

    # Group by buyer, category, supplier
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for award in award_list:
        key = (
            str(award.get("buyer_id") or ""),
            str(award.get("category") or ""),
            str(award.get("supplier_key") or ""),
        )
        if not key[0] or not key[2]:
            continue
        groups[key].append(award)

    findings: list[Finding] = []
    for (buyer_id, category, supplier_key), rows in groups.items():
        rows_sorted = sorted(rows, key=lambda r: _as_date(r["date"]))
        for threshold in ready:
            theta = float(threshold["value"])
            clusters = _window_clusters(
                rows_sorted,
                theta=theta,
                tolerance=tolerance,
                window_days=window_days,
                min_awards=min_awards,
            )
            for cluster in clusters:
                aggregate = sum(float(a["value_amount"]) for a in cluster)
                if aggregate <= theta:
                    continue
                dates = [_as_date(a["date"]) for a in cluster]
                span = (max(dates) - min(dates)).days
                # Mean relative distance below threshold (0 = at threshold).
                distances = [(theta - float(a["value_amount"])) / theta for a in cluster]
                d_bar = sum(distances) / len(distances)
                n = len(cluster)
                score = (
                    float(weights.get("n", 0.25)) * min(n / n_max, 1.0)
                    + float(weights.get("distance", 0.25)) * max(0.0, 1.0 - (d_bar / tolerance))
                    + float(weights.get("span", 0.25)) * max(0.0, 1.0 - (span / window_days))
                    + float(weights.get("aggregate", 0.25)) * min(aggregate / theta, 5.0) / 5.0
                )
                caveats: list[str] = []
                missing_names = sum(1 for a in cluster if not a.get("buyer_name"))
                if missing_names:
                    caveats.append(f"buyer name missing on {missing_names} of {n} records")
                for a in cluster:
                    caveats.extend(a.get("data_caveats") or [])
                findings.append(
                    Finding(
                        type=FindingType.SPLIT,
                        buyer_id=buyer_id,
                        supplier_key=supplier_key,
                        category=category,
                        record_ids=[str(a["record_id"]) for a in cluster],
                        metrics={
                            "count": n,
                            "window_days": window_days,
                            "threshold_id": threshold.get("id"),
                            "threshold_value": theta,
                            "tolerance": tolerance,
                            "aggregate_value": aggregate,
                            "aggregate_to_threshold": round(aggregate / theta, 4),
                            "span_days": span,
                            "mean_distance_below": round(d_bar, 4),
                        },
                        score=round(score, 4),
                        benign_explanations=[
                            "framework agreement",
                            "distinct items",
                            "emergency purchase",
                        ],
                        data_caveats=sorted(set(caveats)),
                        status=FindingStatus.CANDIDATE,
                    )
                )
    return findings


def _window_clusters(
    rows_sorted: list[dict[str, Any]],
    *,
    theta: float,
    tolerance: float,
    window_days: int,
    min_awards: int,
) -> list[list[dict[str, Any]]]:
    """Slide a window and collect near-threshold clusters (deduped by id set)."""
    near = []
    low = theta * (1.0 - tolerance)
    for award in rows_sorted:
        value = float(award["value_amount"])
        # Near-threshold: within tolerance below the threshold (not above).
        if low <= value < theta:
            near.append(award)
    clusters: list[list[dict[str, Any]]] = []
    seen: set[frozenset[str]] = set()
    for i, start in enumerate(near):
        start_date = _as_date(start["date"])
        end_date = start_date + timedelta(days=window_days)
        window = [
            a
            for a in near
            if start_date <= _as_date(a["date"]) <= end_date
        ]
        if len(window) < min_awards:
            continue
        key = frozenset(str(a["record_id"]) for a in window)
        if key in seen:
            continue
        seen.add(key)
        clusters.append(window)
    return clusters


def _as_date(value: Any) -> date:
    """Parse award date fields to date."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
