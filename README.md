# Mizani — Split-Bid Sentinel

Mizani turns Kenya's public procurement award records into a cited, auditable
brief that a procurement committee can act on in minutes. It never awards a
tender and never accuses a supplier. The name is Swahili for *scales*: it weighs
awards against each other and shows where the balance is off.

**Track / theme:** Governance · The Bid Box Challenge · Value for money  
**Licence:** [Apache-2.0](LICENSE)  
**Status:** early build — **steps 1–2 done** (repo scaffold + OCDS ingestion).
Agent, MCP, ledger, and human gate are specified below and land in later steps.

---

## Problem statement

Procurement committees spend about **26 days** sorting bids, checking documents
and writing reports. The patterns that cost public money are the hardest to see
by hand:

| Pattern | What goes wrong |
| --- | --- |
| **Tender splitting** | One purchase is broken into several smaller awards, each just under the value that triggers a competitive method |
| **Price outliers** | The same kind of item is bought at a unit price far from what peers paid, with no stated reason |
| **Repeat-winner concentration** | One supplier takes an unusual share of a buyer's awards in one category |

Kenya's portals publish what was **tendered** and **awarded**, not the bids
themselves (~260k tenders, ~109k awards). So Mizani works the **price,
award-history, and supplier** half of the problem. It does **not** evaluate
bids, and it says so plainly.

The data is messy on purpose: extreme value outliers, supplier identifiers that
break their own scheme, and missing buyer names. Handling that mess is part of
the agentic-depth criterion.

---

## Solution overview

Mizani is an agent that investigates a buyer, category, or time window and
returns a short brief in which **every finding cites the award records behind it**.

1. An open-weights model **plans** the investigation and calls MCP tools.
2. **Deterministic code** does every calculation (split / price / concentration).
3. A separate **verifier** re-derives each finding from raw record IDs.
4. The agent **drafts** a committee brief (and optionally a plain-language digest).
5. A **named human** approves, edits, or rejects — outside the agent's tool set.

### Finding types

| Type | What it flags |
| --- | --- |
| `SPLIT` | Several awards to one supplier from one buyer in one category, each just under a threshold, close in time |
| `PRICE` | A unit price far from the peer distribution for the same item and unit |
| `CONCENTRATION` | One supplier holding an unusual share of a buyer's awards in a category |

Supporting views: **accountability gap** (award vs tender estimate) and **place
lens** (findings by county/buyer with a coverage warning).

### What it will not do

- Award a tender, rank bidders, or recommend a winner
- State or imply wrongdoing (findings list benign explanations)
- Name or profile individuals (company and buyer records only)
- Touch a tender that is still live

---

## Target users

| Role | How they use Mizani |
| --- | --- |
| **Primary:** procurement committee secretary / member | Prepare a defensible file before a meeting |
| **Secondary:** internal auditor | Review the file and evidence ledger afterwards |

Typical ask: *“Review medical supplies awards for this buyer over the last 12 months.”*

### User interview

Not yet recorded — required before day 3 of the build window. Date, role (not
name unless agreed), and two design changes they drove will be listed here.

---

## Architecture

```text
Request
  -> LangGraph agent (plan -> investigate -> verify -> draft -> release_gate)
       |                    |
       | MCP                | MCP
       v                    v
  mizani-mcp (own)     community SQL MCP (borrowed, DuckDB read-only)
       |                    |
       +-----> DuckDB analysis DB (awards, tenders, items, suppliers)
       |
       +-----> SQLite evidence ledger (hash-chained)
       |
       v
  Draft brief (DRAFT) ---- human approve CLI / review page ---- RELEASED
```

| Layer | Role |
| --- | --- |
| Model | Plans, chooses tools, explains — never computes finding numbers |
| Own MCP (`mizani-mcp`) | Domain tools: search, detectors, verify, draft |
| Borrowed SQL MCP | Ad-hoc exploratory queries over DuckDB (read-only) |
| DuckDB | Normalised OCDS tables |
| Ledger | Append-only hash chain of every tool call, finding, and approval |
| Human gate | Outside the agent; no approval MCP tool |

Detailed write-up: `ARCHITECTURE.md` (generated in a later build step).

---

## Agent architecture

Orchestration: **LangGraph** with checkpointing so a paused run survives restart.

| Node | Job | On failure |
| --- | --- | --- |
| `plan` | Turn the request into buyer, category, window, tool plan | Re-prompt once with validation error, then stop clearly |
| `investigate` | Search + detector tools; SQL MCP for exploration | Widen filters up to 2×; retry tool once then skip |
| `verify` | `verify_finding` for every candidate | Failed check → DROPPED + ledger reason |
| `draft` | `draft_committee_brief` with verified IDs only | Schema error: fix and retry once |
| `release_gate` | Graph interrupt until a human decides | Never times out into approval |

**Model (planned):** open-weights via **Ollama**, temperature 0, tool-calling
capable (e.g. Qwen/Llama). Full demo task runs locally. Prompts live in
`prompts/` as versioned text files.

**Status:** graph not implemented yet (build step 7). Package stub:
`src/mizani/agent/`.

---

## MCP implementation

Two MCP servers:

1. **Own server — `mizani-mcp`** (Python, official MCP SDK)  
   Domain analysis only. No pass-through proxy. Every call is logged by a
   ledger wrapper around the tool, not inside the tool body. Structured JSON
   validated with Pydantic v2. No tool releases or publishes a brief.

2. **Borrowed server — community SQL MCP** over the DuckDB file, **read-only**  
   Schema discovery and ad-hoc SQL. Name, version, and licence will be recorded
   in `ARCHITECTURE.md` when pinned.

**Status:** MCP server not implemented yet (build step 6). Package stub:
`src/mizani/mcp_server/`.

---

## MCP tools / servers

### Own server tools (`mizani-mcp`)

| Tool | Kind | Side effect |
| --- | --- | --- |
| `search_awards` | read | None |
| `cluster_threshold_awards` | analysis (SPLIT) | None |
| `benchmark_unit_price` | analysis (PRICE) | None |
| `profile_supplier` | analysis (CONCENTRATION) | None |
| `verify_finding` | check | Ledger entry |
| `draft_committee_brief` | **action** | Writes DRAFT brief + ledger; does **not** release |
| `aggregate_by_place` | stretch | None |
| `award_to_estimate_gap` | stretch | None |

Rules: every finding has a non-empty `record_ids` list; empty citations are
rejected. There is **no** award / rank / recommend-winner tool.

### Borrowed server

Community SQL MCP → DuckDB (`data/processed/mizani.duckdb`), read-only.

---

## Human-in-the-loop workflow

1. Secretary asks for a review (buyer / category / window).
2. Agent plans, queries, runs detectors, verifies, drafts.
3. Graph **pauses** at `release_gate` (interrupt). Status stays `DRAFT`.
4. Named committee member reviews via **CLI** (and optional small web page later).
5. Approve / edit / reject under their own name → ledger records approval.
6. Agent has **no code path** to approve its own work (evals E9 / E10).

Approval is **not** an MCP tool and is not reachable by the model.

**Status:** approval CLI lands in build step 8.

---

## Setup / installation

**Requirements:** Python 3.11+, [uv](https://github.com/astral-sh/uv), Git.

```bash
git clone https://github.com/Nosh-thee-techy/mizani-split-bid-sentinel.git
cd mizani-split-bid-sentinel
uv sync --all-extras
```

Optional Make targets: `make setup` · `make ingest` · `make quality` · `make test`.

Ollama and the MCP / LangGraph demo path will be documented when those steps land
(`make demo`).

---

## Usage instructions

### What works today (steps 1–2)

```bash
# Copy the labelled synthetic sample (not real PPIP data)
mkdir -p data/raw
cp data/samples/synthetic_kenya_shaped.jsonl data/raw/

# Ingest into DuckDB and write a data-quality report
uv run mizani ingest --raw-dir data/raw --sample-label synthetic-sample
uv run mizani quality

# Run tests
uv run pytest -q
```

### Planned agent run (later)

```bash
make demo
# then: mizani approve --brief <path> --approver "Name"
# then: mizani ledger verify
```

---

## Technology stack

| Area | Choice |
| --- | --- |
| Language | Python 3.11+ |
| Env / packaging | uv, hatchling |
| Analysis DB | DuckDB |
| Models / validation | Pydantic v2 |
| Orchestration | LangGraph (planned) |
| Own MCP | Official Python MCP SDK (planned) |
| Borrowed MCP | Community SQL MCP over DuckDB (planned) |
| Local LLM | Ollama + open-weights tool-calling model (planned) |
| CLI | Typer + Rich |
| Name matching | rapidfuzz |
| Ledger | SQLite hash chain (planned) |
| Lint / test | ruff, pytest |

---

## Limitations

- Reads **awards**, not bids — cannot judge bid quality.
- Findings are **indicators**, not proof of wrongdoing.
- Results depend on data completeness and on **correct legal thresholds**
  (`config/thresholds.yaml` is still placeholder `null` until filled from
  Kenyan regulations with a `legal_source`).
- Location fields may be sparse; place lens always shows **coverage** first.
- Framework agreements can look like splits; listed as a benign explanation;
  admitted failures go in `EVALS.md`.
- Open-weights models may fumble tool calls; schemas stay small with re-prompt.
- Current repo: **ingestion + quality report only** — agent/MCP/ledger not shipped yet.

---

## Future improvements

- Fill thresholds from Public Procurement and Asset Disposal regulations (with sources).
- Pull a dated real PPIP / OCP bulk extract and record provenance.
- Build ledger, detectors, verifier, five core MCP tools, LangGraph agent, brief + approve CLI.
- Eval harness E1–E12 and generated `EVALS.md` / `ARCHITECTURE.md`.
- Stretch: place-lens tool, award-to-estimate gap, plain-language digest, minimal review UI.
- Down-weight clusters that look like framework agreements when contract type is present.
- User interview before day 3; record what changed in this README.

---

## Data provenance

| Field | Value |
| --- | --- |
| Intended source | Kenya PPRA / PPIP OCDS (`https://tenders.go.ke/ocds`) |
| OCP registry | https://data.open-contracting.org/en/publication/147 |
| Sample in repo | `data/samples/synthetic_kenya_shaped.jsonl` — **synthetic**, labelled |
| Download date (real) | not yet pulled — confirm endpoint before bulk load |
| Exclusions | Open/live tenders; person-like supplier names; company/buyer records only |

---

## Design disclosure

Some design concepts (place lens, evidence vault, accountability gap, public
legibility) were informed by earlier community accountability work. **All
code, pipelines, and this repository are written fresh** for this challenge.

---

## Repository layout (current)

```text
mizani/
  README.md
  LICENSE                 # Apache-2.0
  Makefile
  pyproject.toml
  config/                 # thresholds, detectors, ingest
  data/samples/           # labelled synthetic OCDS
  src/mizani/
    ingest/               # DONE — loader, filters, canonicalise, DuckDB, quality
    cli.py                # DONE — ingest, quality
    ledger/ detect/ verify/ mcp_server/ agent/ brief/   # stubs for later steps
  tests/
```

---

## Other

- **Challenge constraints mapped in the build spec:** own MCP (3+ tools, one action),
  borrowed MCP, LangGraph, open-weights full task, hash-chained ledger + human
  gate, Apache-2.0, `make demo`, `ARCHITECTURE.md` / `EVALS.md`.
- **Instant disqualifiers guarded:** no award tool; findings require citations;
  no live tenders / personal details; approval outside the agent.
- **Open point:** confirm submission deadline in the community channel (pages
  show 15 Oct vs Rules TBC).
