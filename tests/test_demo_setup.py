"""Tests for setup messaging and demo exit behaviour (Prompt 4)."""

from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from mizani.cli import app
from mizani.demo_flow import check_setup, run_demo

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_setup_mentions_ollama_when_down() -> None:
    rows = check_setup(ROOT)
    by_name = {n: (ok, detail) for n, ok, detail in rows}
    assert "ollama" in by_name
    # If Ollama is down, detail must be clear (not a stack trace).
    ok, detail = by_name["ollama"]
    if not ok:
        assert "Ollama" in detail or "not" in detail.lower()


def test_demo_exits_zero_in_temp_copy(tmp_path: Path) -> None:
    # Minimal clean tree: copy configs + sample + package is importable from src.
    for rel in ("config", "data/samples"):
        shutil.copytree(ROOT / rel, tmp_path / rel)
    summary = run_demo(root=tmp_path, use_real=False)
    assert summary["ledger_ok"] is True
    assert summary["awards_kept"] >= 1


def test_cli_demo_help() -> None:
    result = runner.invoke(app, ["demo", "--help"])
    assert result.exit_code == 0
    assert "--real" in result.stdout
