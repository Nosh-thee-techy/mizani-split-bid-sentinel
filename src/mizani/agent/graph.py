"""LangGraph investigation workflow: plan -> investigate -> verify -> draft -> gate.

Compiled as a real ``StateGraph`` (LangGraph). Nodes call the same domain
functions exposed by ``mizani-mcp`` so ledger wrapping stays consistent.
Borrowed ``mcp-server-duckdb`` remains available for read-only SQL outside the
detector path (see ``docs/borrowed-mcp.md``); detector math never goes through SQL MCP.

The release_gate node sets status awaiting_approval and stops. There is no
approve tool and no code path here that can mark a brief APPROVED.

Planner: deterministic by default (reliable for demos/evals). Set
``planner_mode="ollama"`` only after ``mizani model-check`` recommends a model.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

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


def _resolve(state: AgentState) -> dict[str, Path]:
    """Resolve DuckDB / ledger / brief paths from state or package defaults."""
    root = Path(state.config_root) if state.config_root else ROOT
    return {
        "db": Path(state.db_path) if state.db_path else root / "data" / "processed" / "mizani.duckdb",
        "ledger": (
            Path(state.ledger_path)
            if state.ledger_path
            else root / "data" / "processed" / "evidence_ledger.sqlite"
        ),
        "briefs": Path(state.briefs_dir) if state.briefs_dir else root / "briefs" / "drafts",
        "checkpoints": (
            Path(state.checkpoint_dir)
            if state.checkpoint_dir
            else root / "data" / "processed" / "checkpoints"
        ),
        "root": root,
    }


def refuse_if_award_request(state: AgentState) -> AgentState:
    """Hard refuse prompts that ask the agent to award or name a winner."""
    if _AWARD_INTENT.search(state.request or ""):
        state.status = "refused"
        state.refusal_reason = (
            "Refused: Mizani cannot award tenders, rank bidders, or name winners."
        )
        paths = _resolve(state)
        ledger = Ledger(paths["ledger"])
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


def _deterministic_plan(text: str) -> InvestigationPlan:
    """Parse buyer/category from the request without calling a model."""
    buyer = None
    category = None
    m_buyer = re.search(r"buyer[=:\s]+([^\s,;]+)", text, re.I)
    m_cat = re.search(r"category[=:\s]+([^\n,;]+)", text, re.I)
    if m_buyer:
        buyer = m_buyer.group(1).strip()
    if m_cat:
        category = m_cat.group(1).strip()
    if not category and "medical" in text.lower():
        category = "goods"
    return InvestigationPlan(
        buyer=buyer,
        category=category,
        window_days=365,
        notes="deterministic plan; model explains after tools return",
    )


def _ollama_plan(text: str, model_name: str) -> InvestigationPlan | None:
    """Best-effort Ollama plan; returns None on any failure so we fall back."""
    try:
        import urllib.request

        prompt = (
            "Extract procurement investigation filters as JSON with keys "
            "buyer (string|null), category (string|null), window_days (int). "
            "No prose.\nRequest: "
            + text
        )
        body = json.dumps(
            {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw = payload.get("response") or "{}"
        data = json.loads(raw) if isinstance(raw, str) else raw
        return InvestigationPlan(
            buyer=data.get("buyer"),
            category=data.get("category"),
            window_days=int(data.get("window_days") or 365),
            notes="ollama plan; tools remain deterministic",
        )
    except Exception:
        return None


def plan_node(state: AgentState) -> AgentState:
    """Build a structured plan (Ollama optional; deterministic fallback)."""
    state = refuse_if_award_request(state)
    if state.status == "refused":
        return state
    state.run_id = state.run_id or _new_run_id()
    text = state.request or ""

    plan: InvestigationPlan | None = None
    if state.planner_mode == "ollama" and state.model_name:
        plan = _ollama_plan(text, state.model_name)
    if plan is None:
        plan = _deterministic_plan(text)
        state.planner_mode = "deterministic"

    state.plan = plan
    state.status = "planned"
    return state


def investigate_node(state: AgentState) -> AgentState:
    """Call domain tools; widen filters once if search is empty.

    Also launches dual MCP (own + borrowed DuckDB) and runs one SQL probe via
    the borrowed server so every normal run exercises the MCP client path.
    """
    if state.status == "refused":
        return state
    assert state.plan is not None
    paths = _resolve(state)
    ledger = Ledger(paths["ledger"])
    root = paths["root"]
    thresholds = load_yaml(root / "config" / "thresholds.yaml")
    fixture = root / "evals" / "fixtures" / "thresholds_synthetic.yaml"
    if not fixture.exists():
        fixture = ROOT / "evals" / "fixtures" / "thresholds_synthetic.yaml"
    sensitivity = root / "config" / "thresholds_sensitivity.yaml"
    if not sensitivity.exists():
        sensitivity = ROOT / "config" / "thresholds_sensitivity.yaml"
    # Production thresholds stay null until Second Schedule is pasted.
    # Sensitivity mode is explicitly labelled — never treated as legal.
    use_sensitivity = False
    configured = [
        t
        for t in (thresholds.get("thresholds") or [])
        if t.get("value") is not None and t.get("legal_source")
    ]
    sample = ""
    try:
        import duckdb

        conn = duckdb.connect(str(paths["db"]), read_only=True)
        try:
            row = conn.execute(
                "SELECT value FROM ingest_meta WHERE key='sample_label'"
            ).fetchone()
            sample = (row[0] if row else "") or ""
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        sample = ""
    if not configured:
        if "synthetic" in sample.lower() or sample == "":
            if fixture.exists():
                thresholds = load_yaml(fixture)
        elif sensitivity.exists():
            thresholds = load_yaml(sensitivity)
            use_sensitivity = True
    detectors = load_yaml(root / "config" / "detectors.yaml")
    if not (root / "config" / "detectors.yaml").exists():
        detectors = load_yaml(ROOT / "config" / "detectors.yaml")
    plan = state.plan
    db_path = paths["db"]

    # Dual MCP: own domain server + borrowed read-only DuckDB SQL.
    mcp_probe: dict[str, Any] = {"error": "not_run"}
    try:
        from mizani.agent.mcp_runtime import dual_mcp_probe_sync

        mcp_probe = dual_mcp_probe_sync(db_path, root)
        ledger.append(
            LedgerEntry(
                run_id=state.run_id,
                actor=LedgerActor.AGENT,
                event=LedgerEvent.TOOL_CALL,
                tool="borrowed_mcp_sql_probe",
                inputs={"sql": "SELECT count(*) AS awards_kept FROM awards WHERE NOT excluded"},
                outputs_digest=json.dumps(mcp_probe.get("sql_probe") or {})[:200],
                record_ids=["mcp:borrowed-sql"],
                model=state.model_name or "deterministic",
            )
        )
    except Exception as exc:  # noqa: BLE001
        mcp_probe = {"error": str(exc)}
        ledger.append(
            LedgerEntry(
                run_id=state.run_id,
                actor=LedgerActor.AGENT,
                event=LedgerEvent.TOOL_CALL,
                tool="borrowed_mcp_sql_probe",
                inputs={"failed": True},
                outputs_digest=str(exc)[:200],
                record_ids=["mcp:borrowed-sql-error"],
                model=state.model_name or "deterministic",
            )
        )
    state.mcp_borrowed_sql = mcp_probe.get("sql_probe") or {"error": mcp_probe.get("error")}
    state.sensitivity_thresholds = use_sensitivity
    # Stash probe on plan notes for demo printing (non-secret).
    if state.plan is not None:
        note = state.plan.notes or ""
        sens = " SENSITIVITY_THRESHOLDS" if use_sensitivity else ""
        state.plan.notes = (
            f"{note}; dual_mcp={('ok' if mcp_probe.get('sql_probe') else mcp_probe.get('error'))}{sens}"
        ).strip("; ")

    def _search() -> dict[str, Any]:
        return domain.search_awards(
            db_path,
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
            db_path,
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
        # Auto-switches to value-benchmark when items are absent (real Kenya bulk).
        findings = domain.benchmark_unit_price(db_path, detectors_cfg=detectors)
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
    paths = _resolve(state)
    ledger = Ledger(paths["ledger"])
    db_path = paths["db"]
    verified: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in state.candidate_findings:
        finding = Finding.model_validate(raw)

        def _verify(f: Finding = finding) -> dict[str, Any]:
            return domain.verify_finding_tool(f, db_path=db_path).model_dump(mode="json")

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
    paths = _resolve(state)
    ledger = Ledger(paths["ledger"])
    findings = [Finding.model_validate(v) for v in state.verified_findings]

    def _draft() -> dict[str, Any]:
        return domain.draft_committee_brief(
            findings=findings,
            requester="demo-secretary",
            out_dir=paths["briefs"],
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
    paths = _resolve(state)
    paths["checkpoints"].mkdir(parents=True, exist_ok=True)
    path = paths["checkpoints"] / f"{state.run_id}.json"
    path.write_text(json.dumps(state.to_checkpoint(), indent=2) + "\n", encoding="utf-8")
    return state


def _route_after_plan(state: AgentState) -> str:
    """Stop early on refuse; otherwise continue the investigation."""
    if state.status == "refused":
        return "end"
    return "investigate"


def build_investigation_graph():
    """Compile the LangGraph StateGraph used by ``run_investigation``."""
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("investigate", investigate_node)
    graph.add_node("verify", verify_node)
    graph.add_node("draft", draft_node)
    graph.add_node("release_gate", release_gate_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"investigate": "investigate", "end": END},
    )
    graph.add_edge("investigate", "verify")
    graph.add_edge("verify", "draft")
    graph.add_edge("draft", "release_gate")
    graph.add_edge("release_gate", END)
    return graph.compile()


# Module-level compiled graph (Prompt 10b).
INVESTIGATION_GRAPH = build_investigation_graph()


def run_investigation(
    request: str,
    *,
    model_name: str | None = None,
    db_path: Path | None = None,
    ledger_path: Path | None = None,
    briefs_dir: Path | None = None,
    checkpoint_dir: Path | None = None,
    config_root: Path | None = None,
    planner_mode: str = "deterministic",
) -> AgentState:
    """Run the compiled LangGraph until the release gate (or refuse)."""
    state = AgentState(
        request=request,
        model_name=model_name,
        run_id=_new_run_id(),
        db_path=str(db_path) if db_path else None,
        ledger_path=str(ledger_path) if ledger_path else None,
        briefs_dir=str(briefs_dir) if briefs_dir else None,
        checkpoint_dir=str(checkpoint_dir) if checkpoint_dir else None,
        config_root=str(config_root) if config_root else None,
        planner_mode="ollama" if planner_mode == "ollama" else "deterministic",
    )
    # LangGraph invoke returns a dict-like state for Pydantic schemas.
    result = INVESTIGATION_GRAPH.invoke(state)
    if isinstance(result, AgentState):
        return result
    return AgentState.model_validate(result)


def load_checkpoint(run_id: str, *, checkpoint_dir: Path | None = None) -> AgentState:
    """Load a paused run from disk."""
    base = Path(checkpoint_dir) if checkpoint_dir else CHECKPOINT_DIR
    path = base / f"{run_id}.json"
    return AgentState.from_checkpoint(json.loads(path.read_text(encoding="utf-8")))
