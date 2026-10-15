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



@app.command("fetch")
def fetch_cmd(
    year: int = typer.Option(..., "--year", help="Registry year file, e.g. 2026"),
    sources: Path = typer.Option(
        ROOT / "config" / "sources.yaml",
        "--sources",
        help="Sources YAML (must have verified: true)",
    ),
    out_dir: Path = typer.Option(
        ROOT / "data" / "raw" / "real",
        "--out-dir",
        help="Destination directory (git-ignored)",
    ),
) -> None:
    """Download a verified OCDS year file from the OCP Data Registry (never overwrites)."""
    from mizani.ingest.fetch import FetchError, fetch_year

    try:
        prov = fetch_year(year=year, sources_path=sources, raw_real_dir=out_dir)
    except FetchError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"Downloaded {prov['filename']}")
    console.print(f"JSONL: {prov['jsonl']} ({prov['size_jsonl_bytes']} bytes)")
    console.print(f"sha256_jsonl: {prov['sha256_jsonl']}")
    console.print(f"provenance written beside the file under {out_dir}")


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
    place = report["place_lens"]
    coverage = place["location_coverage"]
    pct = place.get("location_coverage_pct_display")
    table.add_row(
        "LOCATION COVERAGE (county|region)",
        "n/a" if coverage is None else f"{pct}% ({place['located_awards']}/{counts['awards_kept']})",
    )
    presence = report.get("presence_rates") or {}
    for key, label in (
        ("buyer_name", "Presence buyer name"),
        ("award_value", "Presence award value"),
        ("tender_estimate_value", "Presence tender estimate"),
        ("supplier_id", "Presence supplier ID"),
        ("any_delivery_location", "Presence any delivery location"),
    ):
        val = presence.get(key)
        table.add_row(label, "n/a" if val is None else f"{val:.1%}")
    real = report.get("real_data_checks") or {}
    inv = real.get("invalid_or_missing_supplier_id_schemes") or {}
    table.add_row(
        "Invalid/missing supplier ID schemes",
        f"{inv.get('count', 0)} ({inv.get('rate')})",
    )
    table.add_row(
        "Value outliers beyond p01/p99",
        str(report["value_distribution"].get("extreme_outlier_count_beyond_p01_p99", 0)),
    )
    table.add_row(
        "Value outliers >100x median",
        str(report["value_distribution"].get("extreme_outlier_count_gt_100x_median", 0)),
    )
    table.add_row("Currencies", ", ".join(report.get("currencies") or []) or "(none)")
    table.add_row("Open tenders excluded", str(report.get("open_tenders_excluded", 0)))
    table.add_row(
        "Unit-price possible (items>0)",
        str(real.get("unit_price_possible", counts["items_kept"] > 0)),
    )
    console.print(table)
    if coverage is not None:
        console.print(
            f"[bold]LOCATION COVERAGE = {pct}%[/bold] "
            f"({place['located_awards']} located / {counts['awards_kept']} kept awards). "
            "Do not treat gaps as geographic patterns."
        )
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
    planner: str = typer.Option(
        "auto",
        "--planner",
        help="auto | ollama | deterministic (deterministic forced for tests)",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Ollama model tag when planner is ollama/auto",
    ),
) -> None:
    """Run the cross-platform demo through the human release gate."""
    from mizani.demo_flow import run_demo

    try:
        summary = run_demo(
            root=ROOT,
            use_real=real,
            planner=planner,
            model_name=model,
        )
    except FileNotFoundError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    except RuntimeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    console.print("Demo complete.")
    console.print(f"planner: {summary.get('planner_mode')}")
    console.print(f"model: {summary.get('model_name')}")
    console.print(f"run_id: {summary['run_id']}")
    console.print(f"awards_kept: {summary.get('awards_kept')}")
    console.print(f"agent_status: {summary.get('agent_status')}")
    console.print(f"brief: {summary.get('brief_path')}")
    console.print(f"verified_findings: {summary.get('verified_findings')}")
    console.print(f"mcp_borrowed_sql: {summary.get('mcp_borrowed_sql')}")
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
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Ollama model name, or omit with --all to score every tool-capable model",
    ),
    trials: int = typer.Option(20, "--trials", help="Total trials across 4 scenarios"),
    all_models: bool = typer.Option(False, "--all", help="Score all tool-capable installed models"),
) -> None:
    """Score open-weights tool calling via Ollama (Prompt 6b)."""
    from mizani.model_check import run_model_check

    try:
        path = run_model_check(
            model=model,
            trials=trials,
            root=ROOT,
            all_installed=all_models or model is None,
        )
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
