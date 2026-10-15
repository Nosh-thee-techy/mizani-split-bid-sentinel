# Architecture

Mizani prepares cited procurement briefs from Kenya OCDS **award** data. The model plans and explains; deterministic tools compute; a human releases.

## Agent shape

```text
plan -> investigate -> verify -> draft -> release_gate (interrupt)
```

- **plan** — structured buyer/category/window (deterministic parser today; LLM-ready).
- **investigate** — `search_awards` + detectors; widens filters up to twice on empty results.
- **verify** — independent recomputation from record IDs; failures become DROPPED.
- **draft** — writes a **DRAFT** brief only.
- **release_gate** — checkpoints and stops. No approval tool exists on the agent.

## MCP servers

| Server | Role | Why |
| --- | --- | --- |
| **mizani-mcp** (ours) | Domain tools: search, split, price, profile, verify, draft | Analysis is not a pass-through proxy |
| **mcp-server-duckdb** (borrowed) | Read-only SQL over DuckDB | Schema discovery + ad-hoc queries without owning a SQL MCP |

See `docs/borrowed-mcp.md` for version/licence notes.

## Human gate

`mizani approve` / `mizani reject` live in `src/mizani/brief/approve.py`. They are **not** MCP tools and are **not** imported by `src/mizani/agent/`.

## Evidence ledger

SQLite hash chain (`data/processed/evidence_ledger.sqlite`). Every tool call is wrapped by `logged_tool_call`. `mizani ledger verify` walks the chain.

## Data

DuckDB analysis DB from OCDS ingest. Synthetic sample ships in-repo; real PPIP pull waits on confirmed `config/sources.yaml` (`verified: true`).
