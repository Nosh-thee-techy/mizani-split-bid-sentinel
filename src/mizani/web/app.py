"""FastAPI demonstration API for Mizani ingest outputs.

Serves the same JSON snapshots as the static GitHub Pages demo, and can
optionally re-run quality against a local DuckDB file.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


ROOT = Path(__file__).resolve().parents[3]
DEMO_DIR = ROOT / "docs"
API_DIR = DEMO_DIR / "api"
SAMPLES = ROOT / "data" / "samples"


app = FastAPI(
    title="Mizani demo API",
    description="Working demonstration of OCDS ingest + data-quality (steps 1–2).",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _read_json(path: Path) -> dict | list:
    """Load a JSON file or raise 404."""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Missing {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/")
def home() -> FileResponse:
    """Serve the demo HTML page."""
    index = DEMO_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="docs/index.html missing")

    return FileResponse(index)


@app.get("/api/health")
def health() -> dict:
    """Liveness probe for the demonstration service."""
    return _read_json(API_DIR / "health.json")


@app.get("/api/quality")
def quality() -> dict:
    """Return the labelled synthetic data-quality snapshot."""
    path = API_DIR / "quality.json"
    if not path.exists():
        path = SAMPLES / "demo_quality_report.json"
    return _read_json(path)


@app.get("/api/awards")
def awards() -> list:
    """Return awards from the synthetic ingest snapshot."""
    path = API_DIR / "awards.json"
    if not path.exists():
        path = SAMPLES / "demo_awards.json"
    data = _read_json(path)
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="awards payload must be a list")
    return data
