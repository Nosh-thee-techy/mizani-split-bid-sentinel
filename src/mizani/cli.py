"""Mizani command-line interface.

Approval and ledger commands land in later build steps. Step 1–2 expose
ingest and quality only.
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
console = Console()

ROOT = Path(__file__).resolve().parents[2]


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


if __name__ == "__main__":
    app()
