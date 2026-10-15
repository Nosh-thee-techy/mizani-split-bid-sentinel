"""Render Markdown committee briefs (indicators only — never accusations)."""

from __future__ import annotations

from typing import Iterable

from mizani.finding import Finding


def render_brief_markdown(
    *,
    brief_id: str,
    requester: str,
    run_id: str,
    findings: Iterable[Finding],
    status: str = "DRAFT",
) -> str:
    """Build a Markdown brief from verified findings.

    Wording describes indicators for committee review, never wrongdoing.
    """
    findings = list(findings)
    lines = [
        f"# Committee brief `{brief_id}`",
        "",
        f"**Status:** {status}  ",
        f"**Requester:** {requester}  ",
        f"**Run ID:** {run_id}",
        "",
        "## Summary",
        "",
        f"This brief prepares {len(findings)} cited indicator(s) from award "
        "history for human review. Mizani does not award tenders, rank bidders, "
        "or accuse suppliers. Portals publish awards, not bids — bid quality "
        "is out of scope.",
        "",
        "## Findings",
        "",
        "| Type | Buyer | Supplier | Category | Score | Record IDs |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for f in findings:
        lines.append(
            f"| {f.type.value} | {f.buyer_id or ''} | {f.supplier_key or ''} | "
            f"{f.category or ''} | {f.score if f.score is not None else ''} | "
            f"`{'`, `'.join(f.record_ids)}` |"
        )
    lines.extend(["", "## Evidence and caveats", ""])
    for f in findings:
        lines.append(f"### {f.finding_id} ({f.type.value})")
        lines.append("")
        lines.append(f"- Metrics: `{f.metrics}`")
        lines.append(
            "- Benign explanations to rule out: "
            + (", ".join(f.benign_explanations) if f.benign_explanations else "none listed")
        )
        lines.append(
            "- Data caveats: "
            + (", ".join(f.data_caveats) if f.data_caveats else "none")
        )
        lines.append("")
    lines.extend(
        [
            "## Limits",
            "",
            "- Findings are indicators, not proof of wrongdoing.",
            "- SPLIT detection needs thresholds with legal_source in config.",
            "- Results depend on data completeness.",
            "",
            f"_Footer: brief_id={brief_id} run_id={run_id} status={status}_",
            "",
        ]
    )
    return "\n".join(lines)
