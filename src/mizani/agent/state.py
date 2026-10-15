"""LangGraph agent state for a Mizani investigation run."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class InvestigationPlan(BaseModel):
    """Structured plan produced by the plan node."""

    buyer: str | None = None
    category: str | None = None
    window_days: int = 365
    tools: list[str] = Field(
        default_factory=lambda: [
            "search_awards",
            "cluster_threshold_awards",
            "benchmark_unit_price",
            "profile_supplier",
        ]
    )
    notes: str = ""


class AgentState(BaseModel):
    """Persisted graph state (also checkpointed as dict)."""

    request: str = ""
    plan: InvestigationPlan | None = None
    candidate_findings: list[dict[str, Any]] = Field(default_factory=list)
    verified_findings: list[dict[str, Any]] = Field(default_factory=list)
    dropped_findings: list[dict[str, Any]] = Field(default_factory=list)
    investigate_retries: int = 0
    plan_retries: int = 0
    run_id: str = ""
    brief_path: str | None = None
    brief_id: str | None = None
    brief_hash: str | None = None
    status: Literal[
        "new",
        "planned",
        "investigated",
        "verified",
        "drafted",
        "awaiting_approval",
        "approved",
        "rejected",
        "refused",
        "error",
    ] = "new"
    refusal_reason: str | None = None
    model_name: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None

    def to_checkpoint(self) -> dict[str, Any]:
        """Serialize for LangGraph checkpointer / disk."""
        return self.model_dump(mode="json")

    @classmethod
    def from_checkpoint(cls, data: dict[str, Any]) -> AgentState:
        """Restore from checkpoint dict."""
        return cls.model_validate(data)
