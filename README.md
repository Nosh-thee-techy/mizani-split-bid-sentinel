# Mizani

Mizani turns Kenya's public procurement award records into a cited, auditable
brief that a procurement committee can act on in minutes. It never awards a
tender and never accuses a supplier. The name is Swahili for *scales*.

**Status:** early build — steps 1–2 (repo scaffold + OCDS ingestion) only.

## What this solves (half of the problem)

Built on the **price, award-history, and supplier** half of procurement
oversight. It does **not** evaluate bids (Kenya publishes awards/tenders, not
bid documents).

## One-command ingest of the sample

```bash
uv sync --all-extras
# Use the labelled synthetic sample (not real PPIP data)
mkdir -p data/raw && cp data/samples/synthetic_kenya_shaped.jsonl data/raw/
uv run mizani ingest --raw-dir data/raw --sample-label synthetic-sample
uv run mizani quality
```

Or with Make (GNU make): `make setup` then copy the sample and `make ingest`.

## Data provenance

| Field | Value |
| --- | --- |
| Intended source | Kenya PPRA / PPIP OCDS (`https://tenders.go.ke/ocds`) |
| OCP registry | https://data.open-contracting.org/en/publication/147 |
| Sample in repo | `data/samples/synthetic_kenya_shaped.jsonl` — **synthetic**, labelled |
| Download date (real) | not yet pulled — confirm endpoint in community channel before bulk load |
| Exclusions | Open/live tenders; person-like supplier names; company/buyer records only |

## Design disclosure

Some design concepts (place lens, evidence vault, accountability gap, public
legibility) were informed by earlier community accountability work. **All
code, pipelines, and this repository are written fresh** for this challenge.

## Licence

Apache-2.0. See [LICENSE](LICENSE).

## Thresholds

`config/thresholds.yaml` deliberately has `null` values until filled from
Kenyan procurement regulations with a `legal_source` on each entry. Do not
guess numbers.

## User interview

Not yet recorded — required before day 3 of the build window.
