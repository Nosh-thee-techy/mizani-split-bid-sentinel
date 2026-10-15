"""Shared Finding schema used by detectors, verifier, and MCP tools.

record_ids must be non-empty — a finding that cannot cite source records is
rejected at construction time so it can never reach a committee brief.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class FindingType(str, Enum):
    """Detector finding kinds."""

    SPLIT = "SPLIT"
    PRICE = "PRICE"
    CONCENTRATION = "CONCENTRATION"
    GAP = "GAP"


class FindingStatus(str, Enum):
    """Lifecycle of a finding through detect -> verify."""

    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    DROPPED = "DROPPED"
    INSUFFICIENT_PEERS = "INSUFFICIENT_PEERS"
    THRESHOLDS_NOT_CONFIGURED = "THRESHOLDS_NOT_CONFIGURED"


class Finding(BaseModel):
    """One cited indicator produced by deterministic detectors."""

    finding_id: str = Field(default_factory=lambda: f"f_{uuid4().hex[:12]}")
    type: FindingType
    buyer_id: str | None = None
    supplier_key: str | None = None
    category: str | None = None
    record_ids: list[str]
    metrics: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    benign_explanations: list[str] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)
    status: FindingStatus = FindingStatus.CANDIDATE
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    drop_reason: str | None = None

    @field_validator("record_ids")
    @classmethod
    def record_ids_non_empty(cls, value: list[str]) -> list[str]:
        """Reject findings that cannot cite at least one source record."""
        if not value:
            raise ValueError("record_ids must be a non-empty list")
        return value
