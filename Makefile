# Optional convenience wrappers — all logic lives in `uv run mizani ...`.

.PHONY: setup demo test evals ingest quality lint serve-demo

setup:
	uv run mizani setup

ingest:
	uv run mizani ingest --raw-dir data/raw --db data/processed/mizani.duckdb

quality:
	uv run mizani quality --db data/processed/mizani.duckdb --out data/processed/data_quality_report.json

demo:
	uv run mizani demo

test:
	uv run pytest -q

evals:
	uv run mizani evals

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

serve-demo:
	uv sync --extra web
	uv run uvicorn mizani.web.app:app --host 127.0.0.1 --port 8000
