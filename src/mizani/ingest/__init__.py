"""OCDS ingestion: load, filter, canonicalise, and write DuckDB tables."""

# Lazy exports via __getattr__ so importing a submodule (e.g. filters) does not
# pull the full pipeline graph during unit-test collection.

from typing import Any

__all__ = ["run_ingest", "write_quality_report"]


def __getattr__(name: str) -> Any:
    """Load pipeline helpers on first attribute access."""
    if name == "run_ingest":
        from mizani.ingest.pipeline import run_ingest

        return run_ingest
    if name == "write_quality_report":
        from mizani.ingest.quality import write_quality_report

        return write_quality_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
