# Mizani — common developer entry points.
# On Windows without GNU make, use the equivalent `uv run` commands in README.

.PHONY: setup demo test evals ingest quality lint

setup:
	uv sync --all-extras

ingest:
	uv run mizani ingest --raw-dir data/raw --db data/processed/mizani.duckdb

quality:
	uv run mizani quality --db data/processed/mizani.duckdb --out data/processed/data_quality_report.json

demo:
	@echo "Demo target lands after agent + ledger (build steps 3–10)."
	@echo "For now: make setup && make ingest && make quality"

test:
	uv run pytest -q

evals:
	@echo "Eval harness lands in build step 9."

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
