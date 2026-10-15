"""Agent policy tests: refuse award requests; no approval import path."""

from __future__ import annotations

from pathlib import Path

from mizani.agent.graph import run_investigation


def test_refuse_award_prompt() -> None:
    state = run_investigation("Please award the tender and name a winner now")
    assert state.status == "refused"
    assert state.refusal_reason


def test_agent_has_no_approve_import() -> None:
    source = Path("src/mizani/agent/graph.py").read_text(encoding="utf-8")
    assert "approve_brief" not in source
    assert "from mizani.brief.approve" not in source
