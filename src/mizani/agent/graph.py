"""LangGraph investigation workflow: plan -> investigate -> verify -> draft -> gate.

The release_gate node sets status awaiting_approval and stops. There is no
approve tool and no code path here that can mark a brief APPROVED.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mizani.agent.state import AgentState, InvestigationPlan
from mizani.detect.config import load_yaml
from mizani.finding import Finding, FindingStatus
from mizani.ledger import Ledger, logged_tool_call
from mizani.ledger.models import LedgerActor, LedgerEntry, LedgerEvent
from mizani.mcp_server import tools as domain

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "processed" / "mizani.duckdb"
LEDGER_PATH = ROOT / "data" / "processed" / "evidence_ledger.sqlite"
BRIEFS_DIR = ROOT / "briefs" / "drafts"
CHECKPOINT_DIR = ROOT / "data" / "processed" / "checkpoints"

_AWARD_INTENT = re.compile(
    r"\b(award\s+(the\s+)?tender|name\s+a\s+winner|recommend\s+a\s+winner|pick\s+the\s+supplier)\b",
    re.I,
)


def _new_run_id() -> str:
    return f"run_agent_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def refuse_if_award_request(state: AgentState) -> AgentState:
    """Hard refuse prompts that ask the agent to award or name a winner."""
    if _AWARD_INTENT.search(state.request or ""):
        state.status = "refused"
        state.refusal_reason = (
            "Refused: Mizani cannot award tenders, rank bidders, or name winners."
        )
        ledger = Ledger(LEDGER_PATH)
        ledger.append(
            LedgerEntry(
                run_id=state.run_id or _new_run_id(),
                actor=LedgerActor.AGENT,
                event=LedgerEvent.TOOL_CALL,
                tool="refuse_award_request",
                inputs={"request": state.request},
                outputs_digest="refused",
                record_ids=["policy:no-award"],
                model=state.model_name or "policy",
            )
        )
    return state


def plan_node(state: AgentState) -> AgentState:
    """Build a structured plan from the request (deterministic parser).

    LLM planning can wrap this later; keeping a validated schema path ensures
    the demo runs even when the model fumbles tool calls.
    """
    state = refuse_if_award_request(state)
    if state.status == "refused":
        return state
    state.run_id = state.run_id or _new_run_id()
    text = state.request or ""
    buyer = None
    category = None
    # Simple patterns: buyer=<id> category=<name>
    m_buyer = re.search(r"buyer[=:\s]+([^\s,;]+)", text, re.I)
    m_cat = re.search(r"category[=:\s]+([^\n,;]+)", text, re.I)
    if m_buyer:
        buyer = m_buyer.group(1).strip()
    if m_cat:
        category = m_cat.group(1).strip()
    if not category and "medical" in text.lower():
        category = "goods"
    state.plan = InvestigationPlan(
        buyer=buyer,
        category=category,
        window_days=365,
        notes="deterministic plan; model explains after tools return",
    )
    state.status = "planned"
    return state


def investigate_node(state: AgentState) -> AgentState:
    """Call domain tools; widen filters once if search is empty."""
    if state.status == "refused":
        return state
    assert state.plan is not None
    ledger = Ledger(LEDGER_PATH)
    thresholds = load_yaml(ROOT / "config" / "thresholds.yaml")
    # Prefer fixture thresholds for synthetic demo if production thresholds null
    fixture = ROOT / "evals" / "fixtures" / "thresholds_synthetic.yaml"
    if fixture.exists():
        thresholds = load_yaml(fixture)
    detectors = load_yaml(ROOT / "config" / "detectors.yaml")
    plan = state.plan

    def _search() -> dict[str, Any]:
        return domain.search_awards(
            DB_PATH,
            buyer=plan.buyer,
            category=plan.category,
            limit=100,
        ).model_dump(mode="json")

    search = logged_tool_call(
        ledger,
        run_id=state.run_id,
        tool="search_awards",
        inputs={"buyer": plan.buyer, "category": plan.category},
        fn=_search,
        model=state.model_name or "deterministic",
    )
    if search.get("count", 0) == 0 and state.investigate_retries < 2:
        state.investigate_retries += 1
        plan.category = None
        plan.buyer = None
        search = logged_tool_call(
            ledger,
            run_id=state.run_id,
            tool="search_awards",
            inputs={"widened": True},
            fn=_search,
            model=state.model_name or "deterministic",
        )

    candidates: list[Finding] = []

    def _splits() -> list[dict[str, Any]]:
        findings = domain.cluster_threshold_awards(
            DB_PATH,
            thresholds_doc=thresholds,
            detectors_cfg=detectors,
            buyer=plan.buyer,
            category=plan.category,
        )
        return [f.model_dump(mode="json") for f in findings]

    split_payload = logged_tool_call(
        ledger,
        run_id=state.run_id,
        tool="cluster_threshold_awards",
        inputs={},
        fn=_splits,
        model=state.model_name or "deterministic",
    )
    for raw in split_payload:
        candidates.append(Finding.model_validate(raw))

    def _prices() -> list[dict[str, Any]]:
        findings = domain.benchmark_unit_price(DB_PATH, detectors_cfg=detectors)
        return [f.model_dump(mode="json") for f in findings]

    price_payload = logged_tool_call(
        ledger,
        run_id=state.run_id,
        tool="benchmark_unit_price",
        inputs={},
        fn=_prices,
        model=state.model_name or "deterministic",
    )
    for raw in price_payload:
        candidates.append(Finding.model_validate(raw))

    state.candidate_findings = [
        c.model_dump(mode="json")
        for c in candidates
        if c.status == FindingStatus.CANDIDATE
    ]
    state.status = "investigated"
    return state


def verify_node(state: AgentState) -> AgentState:
    """Verify each candidate; drop failures."""
    if state.status == "refused":
        return state
    ledger = Ledger(LEDGER_PATH)
    verified: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in state.candidate_findings:
        finding = Finding.model_validate(raw)

        def _verify(f: Finding = finding) -> dict[str, Any]:
            return domain.verify_finding_tool(f, db_path=DB_PATH).model_dump(mode="json")

        out = logged_tool_call(
            ledger,
            run_id=state.run_id,
            tool="verify_finding",
            inputs={"finding_id": finding.finding_id},
            fn=_verify,
            model=state.model_name or "deterministic",
            record_ids=list(finding.record_ids),
        )
        if out.get("status") == FindingStatus.VERIFIED.value:
            verified.append(out)
        else:
            dropped.append(out)
            ledger.append(
                LedgerEntry(
                    run_id=state.run_id,
                    actor=LedgerActor.AGENT,
                    event=LedgerEvent.VERIFICATION,
                    tool="verify_finding",
                    inputs={"finding_id": finding.finding_id},
                    outputs_digest=out.get("drop_reason") or "dropped",
                    record_ids=list(finding.record_ids),
                    model=state.model_name or "deterministic",
                )
            )
    state.verified_findings = verified
    state.dropped_findings = dropped
    state.status = "verified"
    return state


def draft_node(state: AgentState) -> AgentState:
    """Draft a brief from verified findings only."""
    if state.status == "refused":
        return state
    ledger = Ledger(LEDGER_PATH)
    findings = [Finding.model_validate(v) for v in state.verified_findings]

    def _draft() -> dict[str, Any]:
        return domain.draft_committee_brief(
            findings=findings,
            requester="demo-secretary",
            out_dir=BRIEFS_DIR,
            run_id=state.run_id,
        ).model_dump(mode="json")

    drafted = logged_tool_call(
        ledger,
        run_id=state.run_id,
        tool="draft_committee_brief",
        inputs={"n": len(findings)},
        fn=_draft,
        model=state.model_name or "deterministic",
    )
    ledger.append(
        LedgerEntry(
            run_id=state.run_id,
            actor=LedgerActor.AGENT,
            event=LedgerEvent.BRIEF_DRAFT,
            tool="draft_committee_brief",
            inputs={"brief_id": drafted["brief_id"]},
            outputs_digest=drafted["content_hash"],
            record_ids=drafted.get("finding_ids") or ["brief:empty"],
            model=state.model_name or "deterministic",
        )
    )
    state.brief_id = drafted["brief_id"]
    state.brief_path = drafted["path"]
    state.brief_hash = drafted["content_hash"]
    state.status = "drafted"
    return state


def release_gate_node(state: AgentState) -> AgentState:
    """Pause for human approval — never auto-approves."""
    if state.status == "refused":
        return state
    state.status = "awaiting_approval"
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{state.run_id}.json"
    path.write_text(json.dumps(state.to_checkpoint(), indent=2) + "\n", encoding="utf-8")
    return state


def run_investigation(request: str, *, model_name: str | None = None) -> AgentState:
    """Run the full graph synchronously until the release gate."""
    state = AgentState(request=request, model_name=model_name, run_id=_new_run_id())
    for node in (plan_node, investigate_node, verify_node, draft_node, release_gate_node):
        state = node(state)
        if state.status in {"refused", "error"}:
            break
    return state


def load_checkpoint(run_id: str) -> AgentState:
    """Load a paused run from disk."""
    path = CHECKPOINT_DIR / f"{run_id}.json"
    return AgentState.from_checkpoint(json.loads(path.read_text(encoding="utf-8")))
