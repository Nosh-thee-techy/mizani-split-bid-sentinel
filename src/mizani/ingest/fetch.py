"""Download verified OCDS bulk files from the Open Contracting Data Registry.

Refuses to download unless config/sources.yaml has verified: true for the
selected source. Never overwrites existing raw files.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml


class FetchError(Exception):
    """Raised when a fetch cannot proceed safely."""


def load_sources(path: Path) -> list[dict[str, Any]]:
    """Load the sources list from YAML."""
    with path.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}
    sources = doc.get("sources") or []
    if not isinstance(sources, list):
        raise FetchError(f"sources.yaml must contain a sources list: {path}")
    return sources


def get_verified_source(sources_path: Path, source_id: str | None = None) -> dict[str, Any]:
    """Return the verified source entry or raise with a clear reason."""
    sources = load_sources(sources_path)
    candidates = sources
    if source_id:
        candidates = [s for s in sources if s.get("id") == source_id]
    if not candidates:
        raise FetchError(f"No source found (id={source_id!r}) in {sources_path}")
    source = candidates[0]
    if not source.get("verified") is True:
        raise FetchError(
            "Refusing to download: source is not verified. "
            "Set verified: true in config/sources.yaml only after community confirmation."
        )
    if not source.get("download_pattern"):
        raise FetchError("Source missing download_pattern")
    return source


def _file_for_year(source: dict[str, Any], year: int) -> str:
    """Map a year to a registered filename (e.g. 2026 -> 2026.jsonl.gz)."""
    name = f"{year}.jsonl.gz"
    files = list(source.get("files") or [])
    if name not in files:
        raise FetchError(
            f"Year {year} not in allowed files {files}. "
            "Only listed registry filenames may be downloaded."
        )
    return name


def _sha256_file(path: Path) -> str:
    """Hash a file in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def fetch_year(
    *,
    year: int,
    sources_path: Path,
    raw_real_dir: Path,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Stream-download one year file, decompress, write provenance; never overwrite.

    Inputs:
        year: calendar year (2026 or 2025 for the confirmed registry files).
        sources_path: path to config/sources.yaml.
        raw_real_dir: data/raw/real/ destination (created if needed).
    Outputs:
        Provenance dict (also written next to the jsonl as *.provenance.json).
    """
    source = get_verified_source(sources_path, source_id=source_id)
    filename = _file_for_year(source, year)
    url = str(source["download_pattern"]).format(file=filename)

    raw_real_dir = Path(raw_real_dir)
    raw_real_dir.mkdir(parents=True, exist_ok=True)

    gz_path = raw_real_dir / filename
    jsonl_path = raw_real_dir / filename.replace(".gz", "")
    prov_path = raw_real_dir / f"{jsonl_path.name}.provenance.json"

    if gz_path.exists() or jsonl_path.exists() or prov_path.exists():
        raise FetchError(
            f"Refusing to overwrite existing files for {filename} under {raw_real_dir}. "
            "Delete them explicitly if you intend to re-download."
        )

    # Stream download to .gz
    req = Request(url, headers={"User-Agent": "mizani-fetch/0.1 (research; Apache-2.0)"})
    with urlopen(req, timeout=300) as resp, gz_path.open("wb") as out:
        shutil.copyfileobj(resp, out)

    # Decompress beside it
    with gzip.open(gz_path, "rb") as src, jsonl_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)

    provenance = {
        "url": url,
        "filename": filename,
        "jsonl": jsonl_path.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "size_gz_bytes": gz_path.stat().st_size,
        "size_jsonl_bytes": jsonl_path.stat().st_size,
        "sha256_gz": _sha256_file(gz_path),
        "sha256_jsonl": _sha256_file(jsonl_path),
        "source_id": source.get("id"),
        "registry_publication": source.get("registry_publication"),
        "confirmed_on": source.get("confirmed_on"),
        "confirmed_by": source.get("confirmed_by"),
        "licence_note": source.get("licence_note"),
        "verified": True,
    }
    prov_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return provenance
