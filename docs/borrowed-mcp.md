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
uvx mcp-server-duckdb --db-path data/processed/mizani.duckdb --readonly
```
