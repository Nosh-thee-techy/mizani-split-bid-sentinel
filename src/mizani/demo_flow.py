"""Cross-platform demo orchestration used by `mizani demo`.

Runs only pathlib + Python APIs so Windows PowerShell and Linux bash behave
the same. Extends as later build steps land (detectors, agent, approve).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mizani.ledger import Ledger, logged_tool_call
from mizani.ledger.models import LedgerActor, LedgerEntry, LedgerEvent


def run_demo(
    *,
    root: Path,
    use_real: bool = False,
) -> dict[str, Any]:
    """Execute the current end-to-end demo and return a summary dict.

    Steps today: copy synthetic sample (unless --real), ingest, quality,
    append demo ledger markers, verify the chain.
    """
    from mizani.ingest.pipeline import run_ingest

    root = Path(root)
    sample_src = root / "data" / "samples" / "synthetic_kenya_shaped.jsonl"
    raw_dir = root / "data" / "raw" / ("real" if use_real else "demo")
    db_path = root / "data" / "processed" / "mizani.duckdb"
    quality_out = root / "data" / "processed" / "data_quality_report.json"
    ledger_path = root / "data" / "processed" / "evidence_ledger.sqlite"
    run_id = f"run_demo_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if use_real:
        if not raw_dir.exists() or not any(raw_dir.glob("*.json*")):
            raise FileNotFoundError(
                f"No real OCDS files under {raw_dir}. Confirm the endpoint "
                "and run fetch once config/sources.yaml has verified: true."
            )
        sample_label = "real"
    else:
        if not sample_src.exists():
            raise FileNotFoundError(f"Missing synthetic sample: {sample_src}")
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / sample_src.name
        shutil.copy2(sample_src, target)
        sample_label = "synthetic-sample"

    ledger = Ledger(ledger_path)

    def _ingest() -> dict[str, Any]:
        return run_ingest(
            raw_dir=raw_dir,
            db_path=db_path,
            ingest_config_path=root / "config" / "ingest.yaml",
            detectors_config_path=root / "config" / "detectors.yaml",
            quality_out=quality_out,
            sample_label=sample_label,
        )

    result = logged_tool_call(
        ledger,
        run_id=run_id,
        tool="demo_ingest",
        inputs={"raw_dir": str(raw_dir), "sample_label": sample_label},
        fn=_ingest,
        model="none",
    )

    # Marker entry so demo always leaves a verifiable chain even if ingest wrapper is sole call.
    ledger.append(
        LedgerEntry(
            run_id=run_id,
            actor=LedgerActor.AGENT,
            event=LedgerEvent.TOOL_CALL,
            tool="demo_complete",
            inputs={"step": "ingest+quality"},
            outputs_digest="demo",
            record_ids=["demo:complete"],
            model="none",
        )
    )

    ok, broken, msg = ledger.verify()
    summary = {
        "run_id": run_id,
        "db_path": str(db_path),
        "quality_report": str(quality_out),
        "ledger_path": str(ledger_path),
        "ledger_ok": ok,
        "ledger_message": msg,
        "broken_entry_id": broken,
        "stats": result.get("stats"),
        "awards_kept": (result.get("stats") or {}).get("awards_kept"),
    }
    summary_path = root / "data" / "processed" / "last_demo.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if not ok:
        raise RuntimeError(f"Ledger verify failed: {msg}")
    return summary


def check_setup(root: Path) -> list[tuple[str, bool, str]]:
    """Return (name, ok, detail) rows for environment readiness."""
    import importlib.util
    import shutil as sh
    import sys
    import urllib.request

    rows: list[tuple[str, bool, str]] = []
    py_ok = sys.version_info >= (3, 11)
    rows.append(("python>=3.11", py_ok, sys.version.split()[0]))
    rows.append(("uv", sh.which("uv") is not None, sh.which("uv") or "missing"))
    rows.append(
        (
            "package:duckdb",
            importlib.util.find_spec("duckdb") is not None,
            "importable" if importlib.util.find_spec("duckdb") else "missing — run uv sync",
        )
    )
    sample = root / "data" / "samples" / "synthetic_kenya_shaped.jsonl"
    rows.append(("synthetic sample", sample.exists(), str(sample)))
    ollama_ok = False
    ollama_detail = "not reachable"
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as resp:
            ollama_ok = resp.status == 200
            ollama_detail = "reachable at 127.0.0.1:11434"
    except Exception:  # noqa: BLE001 — setup should never crash
        ollama_detail = "Ollama not running (optional until agent demo)"
    rows.append(("ollama", ollama_ok, ollama_detail))
    return rows
