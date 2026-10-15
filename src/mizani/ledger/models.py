"""Pydantic models for ledger entries and approval payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LedgerActor(str, Enum):
    """Who caused the ledger event."""

    AGENT = "agent"
    HUMAN = "human"


class LedgerEvent(str, Enum):
    """Event kinds stored on the chain."""

    TOOL_CALL = "tool_call"
    FINDING = "finding"
    VERIFICATION = "verification"
    BRIEF_DRAFT = "brief_draft"
    APPROVAL = "approval"
    REJECTION = "rejection"


class ApprovalPayload(BaseModel):
    """Human decision attached to approval / rejection events."""

    approver: str
    decision: str  # approve | reject
    note: str = ""

    @field_validator("approver")
    @classmethod
    def approver_non_empty(cls, value: str) -> str:
        """Reject anonymous approvals — the gate must name a real person."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("approver name must be non-empty")
        return cleaned

    @field_validator("decision")
    @classmethod
    def decision_allowed(cls, value: str) -> str:
        """Only approve or reject are valid decisions."""
        normalised = value.strip().lower()
        if normalised not in {"approve", "reject"}:
            raise ValueError("decision must be 'approve' or 'reject'")
        return normalised


class LedgerEntry(BaseModel):
    """One append-only ledger row (before or after hashing)."""

    entry_id: int | None = None
    run_id: str
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    actor: LedgerActor
    event: LedgerEvent
    tool: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs_digest: str | None = None
    record_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int | None = None
    approval: ApprovalPayload | None = None
    prev_hash: str | None = None
    entry_hash: str | None = None

    def to_chain_dict(self) -> dict[str, Any]:
        """Dict used for hashing / storage (JSON-mode, no None-stripping of keys)."""
        return self.model_dump(mode="json")
