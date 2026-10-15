# Mizani — common developer entry points.
# On Windows without GNU make, use the equivalent `uv run` commands in README.

.PHONY: setup demo test evals ingest quality lint serve-demo

setup:
	uv sync --all-extras

ingest:
	uv run mizani ingest --raw-dir data/raw --db data/processed/mizani.duckdb

quality:
	uv run mizani quality --db data/processed/mizani.duckdb --out data/processed/data_quality_report.json

serve-demo:
	uv sync --extra web
	uv run uvicorn mizani.web.app:app --host 127.0.0.1 --port 8000

demo:
	@echo "Full agent demo lands after ledger + MCP (build steps 3–10)."
	@echo "Static demo: https://nosh-thee-techy.github.io/mizani-split-bid-sentinel/"
	@echo "Local: make serve-demo"

test:
	uv run pytest -q

evals:
	@echo "Eval harness lands in build step 9."

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
