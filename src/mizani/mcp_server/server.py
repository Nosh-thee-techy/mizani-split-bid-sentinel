"""mizani-mcp — own MCP server exposing domain tools (official MCP SDK).

No tool awards, ranks, recommends a winner, approves, or releases a brief.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from mizani.detect.config import load_yaml
from mizani.finding import Finding
from mizani.ledger import Ledger, logged_tool_call
from mizani.mcp_server import tools as domain

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "processed" / "mizani.duckdb"
LEDGER_PATH = ROOT / "data" / "processed" / "evidence_ledger.sqlite"
BRIEFS_DIR = ROOT / "briefs" / "drafts"

mcp = FastMCP("mizani-mcp")


def _run_id() -> str:
    from datetime import datetime, timezone

    return f"run_mcp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _ledger() -> Ledger:
    return Ledger(LEDGER_PATH)


@mcp.tool()
def search_awards(
    buyer: str | None = None,
    supplier: str | None = None,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search awards with filters; returns rows with record IDs."""
    run_id = _run_id()
    inputs = {
        "buyer": buyer,
        "supplier": supplier,
        "category": category,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
    }

    def _call() -> dict[str, Any]:
        return domain.search_awards(
            DB_PATH,
            buyer=buyer,
            supplier=supplier,
            category=category,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        ).model_dump(mode="json")

    return logged_tool_call(
        _ledger(),
        run_id=run_id,
        tool="search_awards",
        inputs=inputs,
        fn=_call,
        model="mcp",
    )


@mcp.tool()
def cluster_threshold_awards(
    buyer: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Detect near-threshold SPLIT clusters (deterministic)."""
    run_id = _run_id()
    thresholds = load_yaml(ROOT / "config" / "thresholds.yaml")
    detectors = load_yaml(ROOT / "config" / "detectors.yaml")

    def _call() -> list[dict[str, Any]]:
        findings = domain.cluster_threshold_awards(
            DB_PATH,
            thresholds_doc=thresholds,
            detectors_cfg=detectors,
            buyer=buyer,
            category=category,
        )
        return [f.model_dump(mode="json") for f in findings]

    return logged_tool_call(
        _ledger(),
        run_id=run_id,
        tool="cluster_threshold_awards",
        inputs={"buyer": buyer, "category": category},
        fn=_call,
        model="mcp",
    )


@mcp.tool()
def benchmark_unit_price(award_record_id: str | None = None) -> list[dict[str, Any]]:
    """Benchmark unit prices; may return INSUFFICIENT_PEERS."""
    run_id = _run_id()
    detectors = load_yaml(ROOT / "config" / "detectors.yaml")

    def _call() -> list[dict[str, Any]]:
        findings = domain.benchmark_unit_price(
            DB_PATH,
            detectors_cfg=detectors,
            award_record_id=award_record_id,
        )
        return [f.model_dump(mode="json") for f in findings]

    return logged_tool_call(
        _ledger(),
        run_id=run_id,
        tool="benchmark_unit_price",
        inputs={"award_record_id": award_record_id},
        fn=_call,
        model="mcp",
    )


@mcp.tool()
def profile_supplier(supplier_key: str) -> dict[str, Any]:
    """Profile a supplier and return concentration indicators."""
    run_id = _run_id()
    detectors = load_yaml(ROOT / "config" / "detectors.yaml")

    def _call() -> dict[str, Any]:
        return domain.profile_supplier(
            DB_PATH,
            supplier_key=supplier_key,
            detectors_cfg=detectors,
        )

    return logged_tool_call(
        _ledger(),
        run_id=run_id,
        tool="profile_supplier",
        inputs={"supplier_key": supplier_key},
        fn=_call,
        model="mcp",
    )


@mcp.tool()
def verify_finding(finding_json: str) -> dict[str, Any]:
    """Re-verify a finding JSON string via the independent verifier."""
    run_id = _run_id()
    finding = Finding.model_validate_json(finding_json)

    def _call() -> dict[str, Any]:
        return domain.verify_finding_tool(finding, db_path=DB_PATH).model_dump(mode="json")

    return logged_tool_call(
        _ledger(),
        run_id=run_id,
        tool="verify_finding",
        inputs={"finding_id": finding.finding_id},
        fn=_call,
        model="mcp",
        record_ids=list(finding.record_ids),
    )


@mcp.tool()
def draft_committee_brief(finding_jsons: list[str], requester: str) -> dict[str, Any]:
    """Write a DRAFT brief from verified findings. Cannot approve or release."""
    run_id = _run_id()
    findings = [Finding.model_validate_json(s) for s in finding_jsons]

    def _call() -> dict[str, Any]:
        return domain.draft_committee_brief(
            findings=findings,
            requester=requester,
            out_dir=BRIEFS_DIR,
            run_id=run_id,
        ).model_dump(mode="json")

    return logged_tool_call(
        _ledger(),
        run_id=run_id,
        tool="draft_committee_brief",
        inputs={"requester": requester, "n_findings": len(findings)},
        fn=_call,
        model="mcp",
    )


@mcp.resource("config://thresholds")
def resource_thresholds() -> str:
    """Expose thresholds.yaml (may contain null values until filled)."""
    return (ROOT / "config" / "thresholds.yaml").read_text(encoding="utf-8")


@mcp.resource("config://detectors")
def resource_detectors() -> str:
    """Expose detectors.yaml."""
    return (ROOT / "config" / "detectors.yaml").read_text(encoding="utf-8")


@mcp.resource("schema://finding")
def resource_finding_schema() -> str:
    """Expose the Finding JSON schema."""
    return json.dumps(Finding.model_json_schema(), indent=2)


@mcp.resource("report://quality-latest")
def resource_quality() -> str:
    """Expose latest data-quality report if present."""
    path = ROOT / "data" / "processed" / "data_quality_report.json"
    if not path.exists():
        return json.dumps({"error": "quality report not generated yet"})
    return path.read_text(encoding="utf-8")


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
