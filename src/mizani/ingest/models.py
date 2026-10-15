"""Normalised row models for DuckDB tables produced by the OCDS loader.

These are flat, analysis-ready records — not a full OCDS re-serialisation.
Every award and item keeps a stable record_id used later for citations.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime as DateTime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TenderRow(BaseModel):
    """One tender / contracting-process tender stage."""

    ocid: str
    tender_id: str
    record_id: str
    title: str | None = None
    description: str | None = None
    status: str | None = None
    procurement_method: str | None = None
    category: str | None = None
    value_amount: float | None = None
    value_currency: str | None = None
    buyer_id: str | None = None
    buyer_name: str | None = None
    county: str | None = None
    region: str | None = None
    tender_period_end: Date | None = None
    date_published: Date | None = None
    release_id: str | None = None
    release_date: DateTime | None = None
    is_open: bool = False
    excluded: bool = False
    exclude_reason: str | None = None


class AwardRow(BaseModel):
    """One award linked to a contracting process."""

    ocid: str
    award_id: str
    record_id: str
    title: str | None = None
    status: str | None = None
    # Field name "date" would shadow datetime.date under PEP 563 / 3.14 eval;
    # annotations use the Date alias imported above.
    date: Date | None = None
    value_amount: float | None = None
    value_currency: str | None = None
    buyer_id: str | None = None
    buyer_name: str | None = None
    supplier_raw_id: str | None = None
    supplier_raw_name: str | None = None
    supplier_key: str | None = None
    supplier_canonical_name: str | None = None
    supplier_match_confidence: float | None = None
    category: str | None = None
    procurement_method: str | None = None
    county: str | None = None
    region: str | None = None
    tender_estimate: float | None = None
    tender_estimate_currency: str | None = None
    supplier_id_scheme: str | None = None
    release_id: str | None = None
    release_date: DateTime | None = None
    excluded: bool = False
    exclude_reason: str | None = None
    data_caveats: list[str] = Field(default_factory=list)



class BuyerRow(BaseModel):
    """Distinct buyer / procuring entity."""

    buyer_id: str
    name: str | None = None
    county: str | None = None
    region: str | None = None


class SupplierRow(BaseModel):
    """Canonical supplier plus raw aliases."""

    supplier_key: str
    canonical_name: str
    raw_ids: list[str] = Field(default_factory=list)
    raw_names: list[str] = Field(default_factory=list)
    is_company: bool = True
    notes: str | None = None


class SupplierAliasRow(BaseModel):
    """One observed (raw_id, raw_name) mapped to a canonical supplier_key."""

    alias_id: str
    supplier_key: str
    raw_id: str | None = None
    raw_name: str
    match_confidence: float
    match_method: str  # exact_id | exact_name | fuzzy_name | singleton


class ItemRow(BaseModel):
    """Line item under an award (for unit-price benchmarks)."""

    ocid: str
    award_id: str
    record_id: str
    item_id: str
    description: str | None = None
    description_norm: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_norm: str | None = None
    unit_value_amount: float | None = None
    unit_value_currency: str | None = None
    total_value_amount: float | None = None
    buyer_id: str | None = None
    supplier_key: str | None = None
    category: str | None = None
    award_date: Date | None = None
    excluded: bool = False
    exclude_reason: str | None = None


class IngestStats(BaseModel):
    """Counters collected during a single ingest run."""

    releases_seen: int = 0
    releases_kept: int = 0
    duplicate_releases_dropped: int = 0
    tenders_raw: int = 0
    tenders_kept: int = 0
    tenders_excluded_open: int = 0
    awards_raw: int = 0
    awards_kept: int = 0
    awards_excluded_open_tender: int = 0
    awards_excluded_person_like: int = 0
    releases_with_empty_awards: int = 0
    invalid_supplier_id_schemes: int = 0
    items_kept: int = 0
    buyers: int = 0
    suppliers_canonical: int = 0
    supplier_aliases: int = 0
    missing_buyer_name: int = 0
    missing_location: int = 0
    currency_set: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


    def add_note(self, message: str) -> None:
        """Append a human-readable ingest note."""
        self.notes.append(message)


def model_to_dict(row: BaseModel) -> dict[str, Any]:
    """Serialize a row for DuckDB insertion (JSON-encode list fields)."""
    data = row.model_dump(mode="json")
    return data


class FindingBase(BaseModel):
    """Shared finding constraints used later by detectors (imported early for schema docs).

    record_ids must be non-empty — empty citations are rejected at validation time.
    """

    finding_id: str
    type: str
    record_ids: list[str]

    @field_validator("record_ids")
    @classmethod
    def record_ids_non_empty(cls, value: list[str]) -> list[str]:
        """Reject findings that cannot cite at least one source record."""
        if not value:
            raise ValueError("record_ids must be a non-empty list")
        return value
