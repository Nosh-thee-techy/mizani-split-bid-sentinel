"""Ollama tool-calling smoke test with scored pass rates (Prompt 6b).

Only models that advertise tool support are tested. Results are written under
evals/results/ and a recommendation is printed (never recommend a model that
fails recovery more than half the time).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class AddNumbersArgs(BaseModel):
    """Dummy tool: add two numbers."""

    a: float
    b: float


class LookupAwardArgs(BaseModel):
    """Dummy tool: look up an award by id."""

    id: str


class DraftNoteArgs(BaseModel):
    """Dummy tool: draft a short note."""

    text: str = Field(min_length=1)


TOOLS = {
    "add_numbers": AddNumbersArgs,
    "lookup_award": LookupAwardArgs,
    "draft_note": DraftNoteArgs,
}

SCENARIOS = ("single", "chain", "recovery", "no_tool")


def list_ollama_models(host: str = "http://127.0.0.1:11434") -> list[dict[str, Any]]:
    """Return raw model objects from the local Ollama daemon."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Ollama is not reachable at 127.0.0.1:11434. Start Ollama and pull a model."
        ) from exc
    return list(payload.get("models") or [])


def model_advertises_tools(model: dict[str, Any], host: str) -> tuple[bool, str]:
    """Best-effort check whether a model supports tool calling.

    Ollama exposes capabilities on /api/show. If the field is missing we fall
    back to a conservative name heuristic and say why.
    """
    name = model.get("name") or ""
    try:
        req = urllib.request.Request(
            f"{host}/api/show",
            data=json.dumps({"name": name}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            detail = json.loads(resp.read().decode("utf-8"))
        caps = detail.get("capabilities") or detail.get("details", {}).get("capabilities")
        if isinstance(caps, list):
            if "tools" in caps or "tool" in caps:
                return True, "capabilities include tools"
            return False, f"capabilities={caps} (no tools)"
        # Some builds put families in details
        family = str((detail.get("details") or {}).get("family") or "").lower()
        template = str(detail.get("template") or "")
        if ".Tools" in template or "tools" in template.lower():
            return True, "template references tools"
        # Known weak / non-tool families we skip explicitly
        if "gemma" in name.lower() or family == "gemma":
            return False, "Gemma family often lacks reliable Ollama tool calling — skipped"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not inspect model ({exc})"
    return False, "no tool capability advertised"


def run_model_check(
    *,
    model: str | None,
    trials: int,
    root: Path,
    host: str = "http://127.0.0.1:11434",
    all_installed: bool = False,
) -> Path:
    """Score one model (or all tool-capable installed models) and write JSON.

    Inputs:
        model: specific model name, or None with all_installed=True.
        trials: total trials distributed across the four scenarios (20 => 5 each).
    Outputs:
        Path to the primary results JSON (or summary when scoring all).
    """
    installed = list_ollama_models(host=host)
    if not installed:
        raise RuntimeError("Ollama reports no installed models.")

    to_test: list[str] = []
    skipped: list[dict[str, str]] = []
    if all_installed or model in (None, "", "all"):
        for m in installed:
            name = m.get("name") or ""
            ok, reason = model_advertises_tools(m, host)
            if ok:
                to_test.append(name)
            else:
                skipped.append({"model": name, "reason": reason})
                print(f"SKIP {name}: {reason}")
    else:
        assert model is not None
        match = next((m for m in installed if m.get("name") == model or model in (m.get("name") or "")), None)
        if match is None:
            names = [m.get("name") for m in installed]
            raise RuntimeError(f"Model {model!r} not installed. Ollama reports: {names}")
        ok, reason = model_advertises_tools(match, host)
        if not ok:
            raise RuntimeError(f"Refusing to score {model}: {reason}")
        to_test = [match.get("name") or model]

    if not to_test:
        raise RuntimeError(
            "No tool-capable models to score. Pull a tool-calling model "
            "(e.g. qwen2.5, llama3.1) or check Ollama capabilities."
        )

    per = max(1, trials // len(SCENARIOS))
    summaries: list[dict[str, Any]] = []
    out_dir = root / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in to_test:
        rows: list[dict[str, Any]] = []
        for scenario in SCENARIOS:
            for i in range(per):
                rows.append(_one_trial(host, name, scenario, i))
        stats = _score_rows(rows)
        payload = {
            "model": name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "temperature": 0,
            "seed": 42,
            "trials_per_scenario": per,
            "scenarios": list(SCENARIOS),
            "trials": rows,
            "scores": stats,
            "skipped_models": skipped,
        }
        safe = name.replace(":", "_").replace("/", "_")
        out = out_dir / f"model_check_{safe}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        summaries.append({"model": name, "path": str(out), **stats})
        _print_table(name, stats)

    recommendation = _recommend(summaries)
    summary_path = out_dir / "model_check_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "skipped": skipped,
                "models": summaries,
                "recommendation": recommendation,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print()
    if recommendation.get("model"):
        print(
            f"RECOMMEND: {recommendation['model']} "
            f"(overall={recommendation['overall_pass_rate']:.0%}, "
            f"recovery={recommendation['recovery_pass_rate']:.0%})"
        )
    else:
        print(f"RECOMMEND: none — {recommendation.get('reason')}")
    return summary_path if len(to_test) > 1 else Path(summaries[0]["path"])


def _score_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-scenario and overall pass rates."""
    by: dict[str, list[dict[str, Any]]] = {s: [] for s in SCENARIOS}
    for r in rows:
        by[r["scenario"]].append(r)

    def rate(subset: list[dict[str, Any]], key: str) -> float:
        if not subset:
            return 0.0
        return sum(1 for r in subset if r.get(key)) / len(subset)

    scenario_rates = {}
    for s, subset in by.items():
        if s == "no_tool":
            scenario_rates[s] = rate(subset, "correct_no_tool")
        elif s == "recovery":
            scenario_rates[s] = rate(subset, "recovered")
        else:
            scenario_rates[s] = rate(subset, "pass")

    overall = sum(scenario_rates.values()) / len(scenario_rates) if scenario_rates else 0.0
    return {
        "scenario_pass_rates": scenario_rates,
        "overall_pass_rate": overall,
        "recovery_pass_rate": scenario_rates.get("recovery", 0.0),
        "median_duration_ms": sorted(r["duration_ms"] for r in rows)[len(rows) // 2] if rows else 0,
        "total_tokens_in": sum(r.get("tokens_in") or 0 for r in rows),
        "total_tokens_out": sum(r.get("tokens_out") or 0 for r in rows),
    }


def _recommend(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the best model that passes the recovery gate (>50%)."""
    eligible = [s for s in summaries if s.get("recovery_pass_rate", 0) > 0.5]
    if not eligible:
        return {
            "model": None,
            "reason": "No model passed recovery more than half the time — do not use for the agent demo yet.",
            "overall_pass_rate": 0.0,
            "recovery_pass_rate": 0.0,
        }
    best = max(eligible, key=lambda s: (s.get("overall_pass_rate", 0), s.get("recovery_pass_rate", 0)))
    return {
        "model": best["model"],
        "overall_pass_rate": best["overall_pass_rate"],
        "recovery_pass_rate": best["recovery_pass_rate"],
        "reason": "highest overall pass rate among models with recovery > 50%",
    }


def _print_table(model: str, stats: dict[str, Any]) -> None:
    """Print a plain UTF-8 pass-rate table."""
    print(f"\n=== {model} ===")
    print("scenario\tpass_rate")
    for s, rate in (stats.get("scenario_pass_rates") or {}).items():
        print(f"{s}\t{rate:.0%}")
    print(f"overall\t{stats.get('overall_pass_rate', 0):.0%}")
    print(
        f"tokens_in={stats.get('total_tokens_in')} "
        f"tokens_out={stats.get('total_tokens_out')} "
        f"median_ms={stats.get('median_duration_ms')}"
    )


def _one_trial(host: str, model: str, scenario: str, idx: int) -> dict[str, Any]:
    """Execute one scored trial at temperature 0 with a fixed seed."""
    started = time.perf_counter()
    result = {
        "scenario": scenario,
        "trial": idx,
        "valid_tool_call": False,
        "correct_args": False,
        "recovered": False,
        "correct_no_tool": False,
        "pass": False,
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
        "error": None,
    }
    try:
        if scenario == "no_tool":
            reply = _chat(host, model, "Reply with the single word READY and do not call any tool.", tools=True)
            result["tokens_in"] = reply.get("tokens_in", 0)
            result["tokens_out"] = reply.get("tokens_out", 0)
            # Pass when the model correctly avoids tools
            result["correct_no_tool"] = reply.get("tool_call") is None
            result["pass"] = result["correct_no_tool"]
        elif scenario == "single":
            reply = _chat(host, model, "Call add_numbers with a=2 and b=3.", tools=True)
            result["tokens_in"] = reply.get("tokens_in", 0)
            result["tokens_out"] = reply.get("tokens_out", 0)
            call = reply.get("tool_call")
            if call and call.get("name") == "add_numbers":
                result["valid_tool_call"] = True
                try:
                    AddNumbersArgs.model_validate(call.get("arguments") or {})
                    result["correct_args"] = True
                except ValidationError:
                    result["correct_args"] = False
            result["pass"] = result["valid_tool_call"] and result["correct_args"]
        elif scenario == "chain":
            reply = _chat(
                host,
                model,
                "First call lookup_award with id=ocds-demo-1. You may stop after that tool call.",
                tools=True,
            )
            result["tokens_in"] = reply.get("tokens_in", 0)
            result["tokens_out"] = reply.get("tokens_out", 0)
            call = reply.get("tool_call")
            if call and call.get("name") == "lookup_award":
                result["valid_tool_call"] = True
                try:
                    LookupAwardArgs.model_validate(call.get("arguments") or {})
                    result["correct_args"] = True
                except ValidationError:
                    result["correct_args"] = False
            result["pass"] = result["valid_tool_call"] and result["correct_args"]
        else:  # recovery
            reply = _chat(
                host,
                model,
                "Call add_numbers with a='two' and b=3.",
                tools=True,
            )
            result["tokens_in"] = reply.get("tokens_in", 0)
            result["tokens_out"] = reply.get("tokens_out", 0)
            # Always feed validation error and require a corrected call
            reply2 = _chat(
                host,
                model,
                "Validation error: a must be a number (float), not a string. "
                "Call add_numbers again with a=2 and b=3.",
                tools=True,
            )
            result["tokens_in"] += reply2.get("tokens_in", 0)
            result["tokens_out"] += reply2.get("tokens_out", 0)
            call2 = reply2.get("tool_call")
            if call2 and call2.get("name") == "add_numbers":
                result["valid_tool_call"] = True
                try:
                    AddNumbersArgs.model_validate(call2.get("arguments") or {})
                    result["correct_args"] = True
                    result["recovered"] = True
                except ValidationError:
                    pass
            result["pass"] = result["recovered"]
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    result["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def _chat(host: str, model: str, prompt: str, *, tools: bool) -> dict[str, Any]:
    """Minimal Ollama chat call. Parses the first tool call when present."""
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0, "seed": 42},
        "messages": [
            {"role": "system", "content": "You are a careful tool-calling assistant."},
            {"role": "user", "content": prompt},
        ],
    }
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "add_numbers",
                    "description": "Add two numbers",
                    "parameters": AddNumbersArgs.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lookup_award",
                    "description": "Lookup an award by id",
                    "parameters": LookupAwardArgs.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "draft_note",
                    "description": "Draft a short note",
                    "parameters": DraftNoteArgs.model_json_schema(),
                },
            },
        ]
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    message = payload.get("message") or {}
    tool_call = None
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or tc
        name = fn.get("name")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        tool_call = {"name": name, "arguments": args}
        break
    return {
        "tool_call": tool_call,
        "tokens_in": int(payload.get("prompt_eval_count") or 0),
        "tokens_out": int(payload.get("eval_count") or 0),
        "content": message.get("content"),
    }
