"""Mizani command-line interface.

Typer is the single cross-platform entry point. Approval is a separate command
(not an MCP tool) and lands with the brief flow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="mizani",
    help="Mizani — cited procurement briefs from Kenya OCDS award data.",
    no_args_is_help=True,
)
ledger_app = typer.Typer(help="Evidence ledger: verify chain integrity and inspect entries.")
app.add_typer(ledger_app, name="ledger")
console = Console()

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "data" / "processed" / "evidence_ledger.sqlite"



@app.command("ingest")
def ingest_cmd(
    raw_dir: Path = typer.Option(
        ROOT / "data" / "raw",
        "--raw-dir",
        help="Directory of OCDS JSON/JSONL files",
    ),
    db: Path = typer.Option(
        ROOT / "data" / "processed" / "mizani.duckdb",
        "--db",
        help="Output DuckDB path",
    ),
    config: Path = typer.Option(
        ROOT / "config" / "ingest.yaml",
        "--config",
        help="Ingest YAML config",
    ),
    detectors: Path = typer.Option(
        ROOT / "config" / "detectors.yaml",
        "--detectors",
        help="Detectors YAML (fuzzy thresholds)",
    ),
    quality_out: Optional[Path] = typer.Option(
        ROOT / "data" / "processed" / "data_quality_report.json",
        "--quality-out",
        help="Write data-quality report JSON here (omit with empty string to skip)",
    ),
    sample_label: Optional[str] = typer.Option(
        None,
        "--sample-label",
        help="Optional label stored in ingest_meta (e.g. synthetic-sample)",
    ),
) -> None:
    """Load OCDS releases, filter, canonicalise suppliers, write DuckDB tables."""
    from mizani.ingest.pipeline import run_ingest

    if not raw_dir.exists():
        console.print(f"[red]Raw dir not found:[/red] {raw_dir}")
        raise typer.Exit(code=1)

    result = run_ingest(
        raw_dir=raw_dir,
        db_path=db,
        ingest_config_path=config,
        detectors_config_path=detectors if detectors.exists() else None,
        quality_out=quality_out,
        sample_label=sample_label,
    )
    stats = result["stats"]
    console.print(f"[green]Ingest complete[/green] -> {result['db_path']}")

    console.print(
        f"Releases kept: {stats['releases_kept']} "
        f"(seen {stats['releases_seen']}, dupes dropped {stats['duplicate_releases_dropped']})"
    )
    console.print(
        f"Awards kept: {stats['awards_kept']} "
        f"(excluded open {stats['awards_excluded_open_tender']}, "
        f"person-like {stats['awards_excluded_person_like']})"
    )
    console.print(
        f"Suppliers: {stats['suppliers_canonical']} canonical, "
        f"{stats['supplier_aliases']} aliases"
    )
    if result.get("quality_report_path"):
        console.print(f"Quality report: {result['quality_report_path']}")


@app.command("quality")
def quality_cmd(
    db: Path = typer.Option(
        ROOT / "data" / "processed" / "mizani.duckdb",
        "--db",
        help="DuckDB path",
    ),
    out: Path = typer.Option(
        ROOT / "data" / "processed" / "data_quality_report.json",
        "--out",
        help="Output JSON path",
    ),
) -> None:
    """Rebuild the data-quality report from an existing DuckDB file."""
    from mizani.ingest.quality import write_quality_report

    if not db.exists():
        console.print(f"[red]Database not found:[/red] {db}")
        raise typer.Exit(code=1)

    report = write_quality_report(db, out)
    _print_quality_summary(report)
    console.print(f"Wrote {out}")


def _print_quality_summary(report: dict) -> None:
    """Pretty-print key quality metrics to the terminal."""
    counts = report["row_counts"]
    table = Table(title="Mizani data-quality summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Tenders kept", str(counts["tenders_kept"]))
    table.add_row("Tenders excluded (open)", str(counts["tenders_excluded_open"]))
    table.add_row("Awards kept", str(counts["awards_kept"]))
    table.add_row("Awards excluded", str(counts["awards_excluded"]))
    table.add_row("Items kept", str(counts["items_kept"]))
    table.add_row("Buyers", str(counts["buyers"]))
    table.add_row("Canonical suppliers", str(counts["suppliers_canonical"]))
    coverage = report["place_lens"]["location_coverage"]
    table.add_row(
        "Location coverage",
        "n/a" if coverage is None else f"{coverage:.1%}",
    )
    missing = report["missing_field_rates"]
    table.add_row(
        "Missing buyer name (kept awards)",
        f"{missing['buyer_name']['count']} ({missing['buyer_name']['rate']})",
    )
    table.add_row(
        "Value outliers beyond p01/p99",
        str(report["value_distribution"].get("extreme_outlier_count_beyond_p01_p99", 0)),
    )
    table.add_row(
        "Value outliers >100x median",
        str(report["value_distribution"].get("extreme_outlier_count_gt_100x_median", 0)),
    )
    console.print(table)
    console.print_json(json.dumps(report, indent=2))


@app.command("setup")
def setup_cmd() -> None:
    """Check Python, uv, DuckDB, sample data, and Ollama reachability."""
    from mizani.demo_flow import check_setup

    rows = check_setup(ROOT)
    table = Table(title="Mizani setup check")
    table.add_column("Check")
    table.add_column("OK")
    table.add_column("Detail")
    all_required_ok = True
    for name, ok, detail in rows:
        table.add_row(name, "yes" if ok else "no", detail)
        if name != "ollama" and not ok:
            all_required_ok = False
    console.print(table)
    if not all_required_ok:
        console.print("Setup incomplete: fix the rows marked no (Ollama is optional for now).")
        raise typer.Exit(code=1)
    ollama_ok = next(ok for name, ok, _ in rows if name == "ollama")
    if not ollama_ok:
        console.print(
            "Note: Ollama is not running. Agent demo (later step) will need it; "
            "ingest/ledger demo can still run."
        )
    console.print("Setup OK for current build steps.")


@app.command("demo")
def demo_cmd(
    real: bool = typer.Option(
        False,
        "--real",
        help="Use data/raw/real if present (requires confirmed source)",
    ),
) -> None:
    """Run the cross-platform demo through the human release gate."""
    from mizani.demo_flow import run_demo

    try:
        summary = run_demo(root=ROOT, use_real=real)
    except FileNotFoundError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    console.print("Demo complete.")
    console.print(f"run_id: {summary['run_id']}")
    console.print(f"awards_kept: {summary.get('awards_kept')}")
    console.print(f"agent_status: {summary.get('agent_status')}")
    console.print(f"brief: {summary.get('brief_path')}")
    console.print(f"verified_findings: {summary.get('verified_findings')}")
    console.print(f"ledger: {summary['ledger_message']}")
    console.print(f"quality: {summary['quality_report']}")
    console.print(
        "Release gate: brief remains DRAFT until `mizani approve --brief ... --as \"Name\"`."
    )


@app.command("approve")
def approve_cmd(
    brief: Path = typer.Option(..., "--brief", help="Path to DRAFT brief .md"),
    as_name: str = typer.Option(..., "--as", help="Named approver (required)"),
    note: str = typer.Option("", "--note", help="Optional approval note"),
    ledger_path: Path = typer.Option(DEFAULT_LEDGER, "--ledger"),
    release_dir: Path = typer.Option(ROOT / "briefs" / "released", "--release-dir"),
) -> None:
    """Human approval — not an MCP tool; records a named approver on the ledger."""
    from mizani.brief.approve import ApprovalError, approve_brief

    try:
        meta = approve_brief(
            brief_path=brief,
            approver=as_name,
            note=note,
            ledger_path=ledger_path,
            release_dir=release_dir,
        )
    except ApprovalError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"APPROVED {meta.get('brief_id')} by {as_name}")
    console.print(f"Released under {release_dir}")


@app.command("reject")
def reject_cmd(
    brief: Path = typer.Option(..., "--brief", help="Path to DRAFT brief .md"),
    as_name: str = typer.Option(..., "--as", help="Named reviewer (required)"),
    note: str = typer.Option(..., "--note", help="Rejection note"),
    ledger_path: Path = typer.Option(DEFAULT_LEDGER, "--ledger"),
) -> None:
    """Human rejection — not an MCP tool."""
    from mizani.brief.approve import ApprovalError, reject_brief

    try:
        meta = reject_brief(
            brief_path=brief,
            approver=as_name,
            note=note,
            ledger_path=ledger_path,
        )
    except ApprovalError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"REJECTED {meta.get('brief_id')} by {as_name}")


@app.command("model-check")
def model_check_cmd(
    model: str = typer.Option(..., "--model", help="Ollama model name"),
    trials: int = typer.Option(20, "--trials"),
) -> None:
    """Smoke-test open-weights tool calling via Ollama before relying on the agent."""
    from mizani.model_check import run_model_check

    try:
        path = run_model_check(model=model, trials=trials, root=ROOT)
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"Wrote {path}")


@ledger_app.command("verify")
def ledger_verify_cmd(
    path: Path = typer.Option(DEFAULT_LEDGER, "--path", help="SQLite ledger path"),
) -> None:
    """Walk the hash chain; exit non-zero on the first broken entry."""
    from mizani.ledger import Ledger

    if not path.exists():
        console.print(f"Ledger not found: {path}")
        raise typer.Exit(code=1)
    ledger = Ledger(path)
    ok, broken, msg = ledger.verify()
    if ok:
        console.print(f"OK: {msg}")
        raise typer.Exit(code=0)
    console.print(f"BROKEN at entry_id={broken}: {msg}")
    raise typer.Exit(code=1)


@ledger_app.command("tail")
def ledger_tail_cmd(
    n: int = typer.Option(10, "--n", "-n", help="Number of recent entries"),
    path: Path = typer.Option(DEFAULT_LEDGER, "--path", help="SQLite ledger path"),
) -> None:
    """Print the most recent ledger entries."""
    from mizani.ledger import Ledger

    if not path.exists():
        console.print(f"Ledger not found: {path}")
        raise typer.Exit(code=1)
    ledger = Ledger(path)
    rows = ledger.tail(n)
    table = Table(title=f"Ledger tail (n={n})")
    table.add_column("id")
    table.add_column("ts")
    table.add_column("actor")
    table.add_column("event")
    table.add_column("tool")
    table.add_column("run_id")
    for e in rows:
        table.add_row(
            str(e.entry_id),
            e.ts,
            e.actor.value,
            e.event.value,
            e.tool or "",
            e.run_id,
        )
    console.print(table)


if __name__ == "__main__":
    app()
