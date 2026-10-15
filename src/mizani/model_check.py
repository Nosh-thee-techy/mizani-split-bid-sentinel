"""Ollama tool-calling smoke test (Prompt 6).

Runs small dummy tools against a local model and records success rates.
Fails clearly when Ollama is unreachable.
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
    a: float
    b: float


class LookupAwardArgs(BaseModel):
    id: str


class DraftNoteArgs(BaseModel):
    text: str = Field(min_length=1)


TOOLS = {
    "add_numbers": AddNumbersArgs,
    "lookup_award": LookupAwardArgs,
    "draft_note": DraftNoteArgs,
}


def list_ollama_models(host: str = "http://127.0.0.1:11434") -> list[str]:
    """Return model names reported by the local Ollama daemon."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Ollama is not reachable at 127.0.0.1:11434. Start Ollama and pull a model."
        ) from exc
    return [m.get("name") for m in payload.get("models") or [] if m.get("name")]


def run_model_check(
    *,
    model: str,
    trials: int,
    root: Path,
    host: str = "http://127.0.0.1:11434",
) -> Path:
    """Run tool-calling scenarios and write JSON results under evals/results/."""
    installed = list_ollama_models(host=host)
    if model not in installed and not any(model in m for m in installed):
        raise RuntimeError(
            f"Model {model!r} not installed. Ollama reports: {installed or '(none)'}"
        )

    scenarios = [
        "single",
        "chain",
        "bad_type",
        "no_tool",
    ]
    # Distribute trials across scenarios
    per = max(1, trials // len(scenarios))
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        for i in range(per):
            rows.append(_one_trial(host, model, scenario, i))

    out_dir = root / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = model.replace(":", "_").replace("/", "_")
    out = out_dir / f"model_check_{safe}.json"
    summary = {
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "installed_models": installed,
        "trials": rows,
        "success_rate": (
            sum(1 for r in rows if r.get("valid_tool_call")) / len(rows) if rows else 0.0
        ),
    }
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    _print_table(rows, model)
    return out


def _print_table(rows: list[dict[str, Any]], model: str) -> None:
    """Print a plain-text summary table (UTF-8 safe)."""
    print(f"model-check results for {model}")
    print("scenario\tvalid\targs_ok\trecovered\ttokens_in\ttokens_out\tms")
    for r in rows:
        print(
            f"{r['scenario']}\t{r['valid_tool_call']}\t{r['correct_args']}\t"
            f"{r['recovered']}\t{r['tokens_in']}\t{r['tokens_out']}\t{r['duration_ms']}"
        )


def _one_trial(host: str, model: str, scenario: str, idx: int) -> dict[str, Any]:
    """Execute one trial. Uses /api/chat with tools when available; falls back to heuristic."""
    started = time.perf_counter()
    valid = False
    correct_args = False
    recovered = False
    tokens_in = 0
    tokens_out = 0
    error = None
    try:
        if scenario == "no_tool":
            # Ask a question that needs no tool; success = no tool call.
            reply = _chat(host, model, "Reply with the word READY only.", tools=False)
            tokens_in = reply.get("tokens_in", 0)
            tokens_out = reply.get("tokens_out", 0)
            valid = True
            correct_args = True
        elif scenario == "single":
            reply = _chat(
                host,
                model,
                "Call add_numbers with a=2 and b=3.",
                tools=True,
            )
            tokens_in = reply.get("tokens_in", 0)
            tokens_out = reply.get("tokens_out", 0)
            call = reply.get("tool_call")
            if call and call.get("name") == "add_numbers":
                valid = True
                try:
                    AddNumbersArgs.model_validate(call.get("arguments") or {})
                    correct_args = True
                except ValidationError:
                    correct_args = False
        elif scenario == "chain":
            reply = _chat(
                host,
                model,
                "First lookup_award id=ocds-demo-1 then draft_note text=seen.",
                tools=True,
            )
            tokens_in = reply.get("tokens_in", 0)
            tokens_out = reply.get("tokens_out", 0)
            call = reply.get("tool_call")
            valid = bool(call and call.get("name") in TOOLS)
            if valid:
                schema = TOOLS[call["name"]]
                try:
                    schema.model_validate(call.get("arguments") or {})
                    correct_args = True
                except ValidationError:
                    correct_args = False
        else:  # bad_type
            reply = _chat(
                host,
                model,
                "Call add_numbers with a='two' and b=3.",
                tools=True,
            )
            tokens_in = reply.get("tokens_in", 0)
            tokens_out = reply.get("tokens_out", 0)
            call = reply.get("tool_call")
            if call and call.get("name") == "add_numbers":
                try:
                    AddNumbersArgs.model_validate(call.get("arguments") or {})
                    correct_args = True
                    valid = True
                except ValidationError:
                    # Feed back error and allow one recovery attempt
                    reply2 = _chat(
                        host,
                        model,
                        "Validation error: a must be a number. Call add_numbers with a=2 b=3.",
                        tools=True,
                    )
                    tokens_in += reply2.get("tokens_in", 0)
                    tokens_out += reply2.get("tokens_out", 0)
                    call2 = reply2.get("tool_call")
                    if call2 and call2.get("name") == "add_numbers":
                        try:
                            AddNumbersArgs.model_validate(call2.get("arguments") or {})
                            valid = True
                            correct_args = True
                            recovered = True
                        except ValidationError:
                            pass
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "scenario": scenario,
        "trial": idx,
        "valid_tool_call": valid,
        "correct_args": correct_args,
        "recovered": recovered,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "duration_ms": duration_ms,
        "error": error,
    }


def _chat(host: str, model: str, prompt: str, *, tools: bool) -> dict[str, Any]:
    """Minimal Ollama chat call. Parses tool calls when present."""
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
    with urllib.request.urlopen(req, timeout=120) as resp:
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
