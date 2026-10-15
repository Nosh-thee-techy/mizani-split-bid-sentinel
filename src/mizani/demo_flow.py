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


def _resolve_planner(
    root: Path,
    *,
    planner: str,
    model_name: str | None,
) -> tuple[str, str | None]:
    """Pick planner/model: auto uses model-check recommendation when recovery >50%.

    Returns:
        (planner_mode, model_name) where planner_mode is deterministic|ollama.
    """
    if planner == "deterministic":
        return "deterministic", model_name or "deterministic"
    summary_path = root / "evals" / "results" / "model_check_summary.json"
    recommended = None
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            rec = data.get("recommendation") or {}
            if rec.get("model") and float(rec.get("recovery_pass_rate") or 0) > 0.5:
                recommended = rec["model"]
        except (json.JSONDecodeError, TypeError, ValueError):
            recommended = None
    if planner == "ollama":
        return "ollama", model_name or recommended or "llama3.2:3b"
    # auto
    if recommended:
        return "ollama", model_name or recommended
    return "deterministic", model_name or "deterministic"


def run_demo(
    *,
    root: Path,
    use_real: bool = False,
    planner: str = "auto",
    model_name: str | None = None,
) -> dict[str, Any]:
    """Execute the current end-to-end demo and return a summary dict.

    Steps today: copy synthetic sample (unless --real), ingest, quality,
    append demo ledger markers, verify the chain.
    """
    from mizani.ingest.pipeline import run_ingest

    root = Path(root)
    planner_mode, resolved_model = _resolve_planner(
        root, planner=planner, model_name=model_name
    )
    sample_src = root / "data" / "samples" / "synthetic_kenya_shaped.jsonl"
    raw_dir = root / "data" / "raw" / ("real" if use_real else "demo")
    db_path = root / "data" / "processed" / "mizani.duckdb"
    quality_out = root / "data" / "processed" / "data_quality_report.json"
    ledger_path = root / "data" / "processed" / "evidence_ledger.sqlite"
    run_id = f"run_demo_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    if use_real:
        # Prefer a tractable head-sample when present (full year JSONL is large and
        # fuzzy canonicalisation is O(n^2)); else use the fetched slice under real/.
        sample_dir = root / "data" / "raw" / "real_sample"
        full_dir = root / "data" / "raw" / "real"
        if sample_dir.exists() and any(sample_dir.glob("*.jsonl")):
            raw_dir = sample_dir
            sample_label = "real-ocds-registry-147-sample"
        else:
            raw_dir = full_dir
            sample_label = "real-ocds-registry-147"
        real_files = [
            p
            for p in raw_dir.glob("*.jsonl")
            if p.is_file() and not p.name.endswith(".provenance.json")
        ]
        if not raw_dir.exists() or not real_files:
            raise FileNotFoundError(
                f"No real OCDS JSONL under {full_dir} (or real_sample/). "
                "Run: uv run mizani fetch --year 2026"
            )
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

    # Investigation through the gate. Planner/model printed by CLI.
    # Tests should pass planner="deterministic". No self-approval path.
    from mizani.agent.graph import run_investigation

    agent_state = run_investigation(
        "Review awards for buyer=buyer-nairobi-health category=goods over 12 months",
        model_name=resolved_model,
        db_path=db_path,
        ledger_path=ledger_path,
        briefs_dir=root / "briefs" / "drafts",
        checkpoint_dir=root / "data" / "processed" / "checkpoints",
        config_root=root,
        planner_mode=planner_mode,
    )

    ledger.append(
        LedgerEntry(
            run_id=run_id,
            actor=LedgerActor.AGENT,
            event=LedgerEvent.TOOL_CALL,
            tool="demo_complete",
            inputs={
                "step": "ingest+investigate+verify+draft+gate",
                "agent_run_id": agent_state.run_id,
                "agent_status": agent_state.status,
                "planner_mode": agent_state.planner_mode,
                "model_name": agent_state.model_name,
            },
            outputs_digest=agent_state.brief_hash or "no-brief",
            record_ids=[agent_state.brief_id or "demo:complete"],
            model=agent_state.model_name or planner_mode,
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
        "agent_run_id": agent_state.run_id,
        "agent_status": agent_state.status,
        "brief_path": agent_state.brief_path,
        "brief_id": agent_state.brief_id,
        "verified_findings": len(agent_state.verified_findings),
        "dropped_findings": len(agent_state.dropped_findings),
        "planner_mode": agent_state.planner_mode,
        "model_name": agent_state.model_name,
        "mcp_borrowed_sql": agent_state.mcp_borrowed_sql,
        "sensitivity_thresholds": agent_state.sensitivity_thresholds,
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
