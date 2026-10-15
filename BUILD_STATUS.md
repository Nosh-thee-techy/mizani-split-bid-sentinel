# Build status & continuation notes

**Date:** 2026-10-15  
**Repo:** https://github.com/Nosh-thee-techy/mizani-split-bid-sentinel  
**Demo:** https://nosh-thee-techy.github.io/mizani-split-bid-sentinel/ (**static** Pages snapshot, not a live agent)  
**For:** Blessings — pick up here after chores

## Prompt pack progress

| # | Prompt | Status | Notes |
| --- | --- | --- | --- |
| 1–2 | Scaffold + OCDS ingest | **Done** | DuckDB, quality report, synthetic sample |
| 3 | Evidence ledger | **Done** | Hash chain, verify/tail CLI, tamper tests |
| 4 | Cross-platform CLI + demo + CI | **Done** | `mizani setup/demo`, GHA ubuntu+windows |
| 5 | Real OCDS pull | **Done (5b)** | Registry pub **147**; `sources.yaml` verified; `mizani fetch --year 2026`; head-2500 ingest + quality; value-benchmark PRICE mode |
| 6 | Model tool-calling smoke | **Partial** | `mizani model-check` scoring ready; run `--all --trials 20` when Ollama has a tool-capable tag |
| 7 | Detectors | **Done** | SPLIT/PRICE/CONCENTRATION/GAP; real PRICE = value benchmark |
| 8 | Verifier | **Done** | Independent path; corrupt/missing tests |
| 9 | MCP servers | **Mostly done** | `mizani-mcp` + borrowed `mcp-server-duckdb` docs |
| 10 | LangGraph agent | **Done (compiled)** | Real `StateGraph`; deterministic planner default; optional Ollama plan; gate never self-approves |
| 11 | Briefs + approval | **Done** | Draft briefs; `mizani approve` / `reject` outside agent |
| 12 | Eval harness | **Done** | E1–E12 harness |
| 13 | Docs / interview template | **Done** | README, ARCHITECTURE.md, `docs/interview.md` |
| 14 | Stretch | **Not started** | place lens tool, digest — only if time |
| 15 | Pre-submission audit | **Not run formally** | Use Prompt 15 checklist next |

## Real data (Prompt 5) — read this

- Source: Open Contracting Data Registry publication **147** (Kenya PPRA / PPIP republish).  
  Download only via `https://data.open-contracting.org/en/publication/147/download?name=<FILE>`.
- Full `2026.jsonl` fetched + provenance under `data/raw/real/` (git-ignored).
- Analysis slice: first **2500** lines → `data/raw/real_sample/` → `mizani_real.duckdb`.
- Quality: `data/processed/data_quality_report_real.json`
- Surprises vs synthetic: `data/processed/real_vs_synthetic_surprises.md`
- **LOCATION COVERAGE = 0.0%** on the real slice (print on every place view).
- Unit-price PRICE **cannot** run on real data (`items_kept=0`). Use **value benchmark within category**.

### What breaks on real (filters unchanged)

| Area | Break | Proposed fix |
| --- | --- | --- |
| PRICE unit | No items/units | Value-benchmark mode (shipped) |
| GAP | No tender estimates | Skip / DATA_UNAVAILABLE |
| SPLIT dates | No award.date | Fallback to release_date / contractPeriod at ingest |
| Open filter | tender.status always null | Document inert; no invented statuses |
| Place lens | Buyer location empty | Careful party/delivery extraction; ignore country-only KENYA |
| Schemes | `Bidders` on all IDs | Flagged invalid in quality report |

## What works

```text
uv sync --all-extras
uv run mizani setup
uv run mizani demo
uv run mizani fetch --year 2026
uv run mizani demo --real
uv run mizani quality --db data/processed/mizani_real.duckdb --out data/processed/data_quality_report_real.json
uv run mizani ledger verify
uv run pytest -q
uv run python evals/run_evals.py
```

## Still incomplete / human

1. Legal thresholds — `config/thresholds.yaml` stays null until regulations cited.
2. Score Ollama models (`mizani model-check --all --trials 20`) and only then set planner to ollama.
3. User interview (`docs/interview.md`).
4. Demo video + Prompt 15 audit.
5. Full-year real ingest needs fuzzy scaling (cap shipped; still slow for ~full file).

## Test snapshot

- `pytest`: **29 passed** (2026-10-15)
- Real quality: LOCATION COVERAGE **0.0%**; items **0**; invalid schemes **100%** (`Bidders`)
