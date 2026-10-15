# Build status & continuation notes

**Date:** 2026-10-15  
**Repo:** https://github.com/Nosh-thee-techy/mizani-split-bid-sentinel  
**Demo:** https://nosh-thee-techy.github.io/mizani-split-bid-sentinel/  
**For:** Blessings — pick up here after chores

## Prompt pack progress

| # | Prompt | Status | Notes |
| --- | --- | --- | --- |
| 1–2 | Scaffold + OCDS ingest | **Done** | DuckDB, quality report,  synthetic sample |
| 3 | Evidence ledger | **Done** | Hash chain, verify/tail CLI, tamper tests |
| 4 | Cross-platform CLI + demo + CI | **Done** | `mizani setup/demo`, GHA ubuntu+windows |
| 5 | Real OCDS pull | **Skipped** | Needs community-confirmed endpoint + `sources.yaml` verified:true |
| 6 | Model tool-calling smoke | **Partial** | `mizani model-check` implemented; Ollama has `gemma2:9b` / `gemma2:2b` — **tool-calling reliability not fully scored yet** (run `uv run mizani model-check --model gemma2:9b --trials 20`) |
| 7 | Detectors | **Done** | SPLIT/PRICE/CONCENTRATION/GAP; null thresholds → THRESHOLDS_NOT_CONFIGURED |
| 8 | Verifier | **Done** | Independent path; corrupt/missing tests |
| 9 | MCP servers | **Mostly done** | `mizani-mcp` FastMCP server + domain tools; borrowed = `mcp-server-duckdb` documented in `docs/borrowed-mcp.md` (**confirm licence on pin**) |
| 10 | LangGraph agent | **Partial / pragmatic** | Full flow plan→…→gate works **deterministically**; LangGraph dep installed but graph is hand-orchestrated functions (same nodes). LLM planner not wired to Ollama yet |
| 11 | Briefs + approval | **Done** | Draft briefs; `mizani approve` / `reject` outside agent |
| 12 | Eval harness | **Done** | E1–E12 harness; `EVALS.md` generated; all passing at last run |
| 13 | Docs / interview template | **Done** | README, ARCHITECTURE.md, `docs/interview.md` |
| 14 | Stretch | **Not started** | place lens tool, gap MCP tool, digest — only if time |
| 15 | Pre-submission audit | **Not run formally** | Use Prompt 15 checklist next |

## What works (you can run)

```text
uv sync --all-extras
uv run mizani setup
uv run mizani demo          # ingest → investigate → verify → DRAFT brief → gate
uv run mizani ledger verify
uv run pytest -q
uv run python evals/run_evals.py
```

After demo, approve manually:

```text
uv run mizani approve --brief briefs/drafts/<brief>.md --as "Your Name" --note "ok"
```

## What does not work / is incomplete

1. **Real Kenya OCDS download** — blocked on endpoint confirmation (`config/sources.yaml` has `verified: false`).
2. **Legal thresholds** — `config/thresholds.yaml` values are null; demo/evals use `evals/fixtures/thresholds_synthetic.yaml` only.
3. **Open-weights full agent loop** — demo uses deterministic planner, not Ollama tool calls. Model-check CLI exists; gemma2 may be weak at tools — consider a Qwen/Llama tool-capable tag.
4. **True LangGraph compiled graph + interrupt/checkpointer** — logic matches the nodes; persistence is JSON checkpoint files, not LangGraph SqliteSaver yet.
5. **Borrowed MCP live integration in the agent** — documented and choosable; agent does not yet spawn `mcp-server-duckdb` as a subprocess client.
6. **User interview** — template only (`docs/interview.md`); not filled.
7. **`make demo` agent video path** — CLI demo works; unedited video + honest failure still on human checklist.
8. **Vercel deploy** — auth timed out earlier; GitHub Pages demo is the live link.
9. **Stretch tools** — `aggregate_by_place`, digest, etc. not built.

## Difficulties faced

| Issue | What we did |
| --- | --- |
| Python 3.14 + field named `date` broke Pydantic | Aliased `date as Date` in models |
| Windows console choked on Unicode arrow | ASCII `->` in CLI |
| GitHub Pages only allows `/` or `/docs` | Moved static demo to `docs/` |
| Vercel MCP/CLI auth timeout | Used GitHub Pages instead |
| PRICE outlier hid inside its own peer MAD | Exclude target from peers; handle MAD=0 with 2× relative rule |
| Existing GitHub repo name `mizani` taken | Created `mizani-split-bid-sentinel` |
| Thresholds must not be invented | Synthetic fixture thresholds labelled; production YAML stays null |
| Prompt 5 requires confirmed URL | Explicitly skipped |

## Suggested next actions (human + agent)

1. Confirm PPIP OCDS endpoint → fill `config/sources.yaml` → Prompt 5 fetch.  
2. Fill `thresholds.yaml` from regulations with `legal_source`.  
3. Run `mizani model-check --model <tool-capable> --trials 20` and pick the model.  
4. Wire Ollama into `plan_node` / tool calling; replace hand loop with compiled LangGraph + interrupt.  
5. Do the user interview; paste into README.  
6. Confirm borrowed MCP licence string from the installed package.  
7. Record the <3 min demo video from `mizani demo` + approve + ledger tamper.  
8. Run Prompt 15 audit; only fix what you approve.

## Test snapshot (last local run)

- `pytest`: **26 passed**
- `evals/run_evals.py`: **E1–E12 passed** (EVALS.md regenerated)
- `mizani demo`: **awaiting_approval**, ledger verifies, DRAFT brief written
