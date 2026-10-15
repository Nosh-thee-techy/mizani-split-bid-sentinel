# Borrowed MCP server

## Choice (provisional — confirm with team)

| Field | Value |
| --- | --- |
| Name | `mcp-server-duckdb` |
| Install | `uvx mcp-server-duckdb --db-path <path> --readonly` |
| PyPI | https://pypi.org/project/mcp-server-duckdb/ |
| Version recorded | 1.1.0 (as of build notes Oct 2026) |
| Licence | Check PyPI/project LICENSE on pin (commonly MIT — **confirm on install**) |
| Read-only | `--readonly` opens DuckDB with `read_only=True` and refuses writes |

## Why this beats writing our own

It already handles schema discovery and arbitrary exploratory SQL safely. Mizani's own server stays focused on domain detectors, verification, and drafting.

## Candidates considered

| Package | Notes |
| --- | --- |
| `mcp-server-duckdb` (PyPI) | Explicit `--readonly`; Python; fits our stack |
| `mcp-duckdb-local` (npm) | Node; read-only by default in docs |
| DuckDB `duckdb_mcp` community extension | Different shape (SQL-side MCP); heavier ops |

## Run (read-only)

```bash
# Pin mcp<2 — latest uvx default pulls mcp 2.x and breaks list_resources.
uvx --with "mcp>=1.0,<2" mcp-server-duckdb==1.1.0 --db-path data/processed/mizani.duckdb --readonly
```

Licence on pin: check the installed package metadata (`uvx` cache / PyPI). Commonly MIT — confirm before submission.