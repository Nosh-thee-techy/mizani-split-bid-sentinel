"""Supplier identifier and name canonicalisation.

Messy IDs and near-duplicate names are common in OCDS. We normalise case and
punctuation, exact-match on IDs where possible, then fuzzy-match names above
a high threshold. Every merge is recorded in the alias table with confidence.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from mizani.ingest.models import SupplierAliasRow, SupplierRow


_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace for matching.

    Inputs:
        name: raw organisation name.
    Outputs:
        Normalised string suitable for exact/fuzzy comparison.
    """
    text = name.strip().lower()
    text = _PUNCT.sub(" ", text)
    text = _SPACE.sub(" ", text).strip()
    # Drop ubiquitous legal suffixes for matching (kept in display name).
    for suffix in (
        " limited",
        " ltd",
        " plc",
        " inc",
        " co",
        " company",
        " enterprises",
        " enterprise",
    ):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def normalise_id(raw_id: str | None) -> str | None:
    """Normalise a supplier identifier: strip, upper-case, drop spaces."""
    if raw_id is None:
        return None
    cleaned = re.sub(r"\s+", "", raw_id.strip().upper())
    return cleaned or None


@dataclass
class _Cluster:
    """In-memory cluster of raw supplier sightings sharing one canonical key."""

    supplier_key: str
    canonical_name: str
    raw_ids: set[str] = field(default_factory=set)
    raw_names: set[str] = field(default_factory=set)
    name_norms: set[str] = field(default_factory=set)


@dataclass
class CanonicalisationResult:
    """Outputs of a full pass over observed suppliers."""

    suppliers: list[SupplierRow]
    aliases: list[SupplierAliasRow]
    # Map (normalised_id or name_norm) helpers used when tagging awards.
    key_by_raw_id: dict[str, str]
    key_by_name_norm: dict[str, str]
    confidence_by_alias: dict[str, float]  # alias_id -> confidence


class SupplierCanonicaliser:
    """Build canonical supplier keys from raw (id, name) pairs.

    Why a class: the same instance is reused when tagging award rows so
    aliases stay consistent within one ingest run.
    """

    def __init__(self, *, fuzzy_threshold: float = 92.0, name_min_length: int = 3) -> None:
        """Configure matching thresholds from detectors.yaml / ingest config."""
        self.fuzzy_threshold = fuzzy_threshold
        self.name_min_length = name_min_length
        self._clusters: list[_Cluster] = []
        self._id_index: dict[str, _Cluster] = {}
        self._name_index: dict[str, _Cluster] = {}
        self._aliases: list[SupplierAliasRow] = []

    def observe(self, raw_id: str | None, raw_name: str | None) -> str | None:
        """Register a supplier sighting and return its canonical supplier_key.

        Inputs:
            raw_id: OCDS party id or identifier value (may be None).
            raw_name: organisation name (may be None).
        Outputs:
            supplier_key, or None if there is nothing usable to index.
        """
        name = (raw_name or "").strip()
        nid = normalise_id(raw_id)
        name_norm = normalise_name(name) if name else ""

        if not nid and len(name_norm) < self.name_min_length:
            return None

        cluster: _Cluster | None = None
        method = "singleton"
        confidence = 1.0

        if nid and nid in self._id_index:
            cluster = self._id_index[nid]
            method = "exact_id"
            confidence = 1.0
        elif name_norm and name_norm in self._name_index:
            cluster = self._name_index[name_norm]
            method = "exact_name"
            confidence = 1.0
        elif name_norm and len(name_norm) >= self.name_min_length:
            cluster, score = self._fuzzy_find(name_norm)
            if cluster is not None:
                method = "fuzzy_name"
                confidence = score / 100.0

        if cluster is None:
            key = f"sup_{uuid.uuid4().hex[:12]}"
            display = name or nid or key
            cluster = _Cluster(supplier_key=key, canonical_name=display)
            self._clusters.append(cluster)

        if nid:
            cluster.raw_ids.add(nid)
            self._id_index[nid] = cluster
        if name:
            cluster.raw_names.add(name)
            # Prefer a longer, richer display name as canonical.
            if len(name) > len(cluster.canonical_name):
                cluster.canonical_name = name
        if name_norm:
            cluster.name_norms.add(name_norm)
            self._name_index[name_norm] = cluster

        alias_id = f"alias_{uuid.uuid4().hex[:12]}"
        self._aliases.append(
            SupplierAliasRow(
                alias_id=alias_id,
                supplier_key=cluster.supplier_key,
                raw_id=nid,
                raw_name=name or nid or cluster.canonical_name,
                match_confidence=confidence,
                match_method=method,
            )
        )
        return cluster.supplier_key

    def _fuzzy_find(self, name_norm: str) -> tuple[_Cluster | None, float]:
        """Find the best existing cluster by fuzzy name match above threshold."""
        best: _Cluster | None = None
        best_score = 0.0
        for cluster in self._clusters:
            for existing in cluster.name_norms:
                score = float(fuzz.token_sort_ratio(name_norm, existing))
                if score > best_score:
                    best_score = score
                    best = cluster
        if best is not None and best_score >= self.fuzzy_threshold:
            return best, best_score
        return None, 0.0

    def result(self) -> CanonicalisationResult:
        """Freeze clusters into SupplierRow / alias tables for DuckDB."""
        suppliers = [
            SupplierRow(
                supplier_key=c.supplier_key,
                canonical_name=c.canonical_name,
                raw_ids=sorted(c.raw_ids),
                raw_names=sorted(c.raw_names),
                is_company=True,
            )
            for c in self._clusters
        ]
        key_by_raw_id = {nid: c.supplier_key for nid, c in self._id_index.items()}
        key_by_name_norm = {nn: c.supplier_key for nn, c in self._name_index.items()}
        confidence_by_alias = {a.alias_id: a.match_confidence for a in self._aliases}
        return CanonicalisationResult(
            suppliers=suppliers,
            aliases=list(self._aliases),
            key_by_raw_id=key_by_raw_id,
            key_by_name_norm=key_by_name_norm,
            confidence_by_alias=confidence_by_alias,
        )

    def resolve(self, raw_id: str | None, raw_name: str | None) -> tuple[str | None, float | None, str | None]:
        """Look up an already-observed supplier without creating a new cluster.

        Returns:
            (supplier_key, confidence, canonical_name) or (None, None, None).
        """
        nid = normalise_id(raw_id)
        name_norm = normalise_name(raw_name) if raw_name else ""
        cluster: _Cluster | None = None
        confidence: float | None = None
        if nid and nid in self._id_index:
            cluster = self._id_index[nid]
            confidence = 1.0
        elif name_norm and name_norm in self._name_index:
            cluster = self._name_index[name_norm]
            confidence = 1.0
        elif name_norm:
            cluster, score = self._fuzzy_find(name_norm)
            confidence = score / 100.0 if cluster else None
        if cluster is None:
            return None, None, None
        return cluster.supplier_key, confidence, cluster.canonical_name
