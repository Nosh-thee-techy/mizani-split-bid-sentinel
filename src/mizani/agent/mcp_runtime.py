"""Launch and call both MCP servers as stdio subprocesses.

Own server: ``mizani-mcp`` (domain tools).
Borrowed server: ``mcp-server-duckdb`` (read-only SQL).

Every normal investigation should issue at least one SQL query through the
borrowed server so the dual-MCP path is exercised, not just documented.
"""

from __future__ import annotations

import json
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class DualMcpSession:
    """Open stdio sessions to both Mizani and DuckDB MCP servers."""

    db_path: Path
    root: Path
    own: ClientSession | None = None
    borrowed: ClientSession | None = None
    sql_results: list[dict[str, Any]] = field(default_factory=list)
    _stack: AsyncExitStack | None = None

    async def __aenter__(self) -> DualMcpSession:
        """Start both MCP subprocesses and initialise sessions."""
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        # Own domain MCP (module entrypoint).
        own_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mizani.mcp_server.server"],
            env=None,
            cwd=str(self.root),
        )
        own_transport = await self._stack.enter_async_context(stdio_client(own_params))
        own_read, own_write = own_transport
        self.own = await self._stack.enter_async_context(ClientSession(own_read, own_write))
        await self.own.initialize()

        # Borrowed read-only DuckDB MCP via uvx.
        # Pin package + mcp<2 so uvx's ephemeral env matches our FastMCP v1 stack
        # (latest mcp-server-duckdb + mcp 2.x crashes on list_resources).
        borrowed_params = StdioServerParameters(
            command="uvx",
            args=[
                "--with",
                "mcp>=1.0,<2",
                "mcp-server-duckdb==1.1.0",
                "--db-path",
                str(self.db_path),
                "--readonly",
            ],
            env=None,
            cwd=str(self.root),
        )
        borrowed_transport = await self._stack.enter_async_context(
            stdio_client(borrowed_params)
        )
        b_read, b_write = borrowed_transport
        self.borrowed = await self._stack.enter_async_context(
            ClientSession(b_read, b_write)
        )
        await self.borrowed.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Tear down both MCP subprocesses."""
        if self._stack is not None:
            await self._stack.__aexit__(*exc)
        self.own = None
        self.borrowed = None

    async def list_tool_names(self) -> dict[str, list[str]]:
        """Return tool names from each server (for diagnostics)."""
        assert self.own is not None and self.borrowed is not None
        own_tools = await self.own.list_tools()
        borrowed_tools = await self.borrowed.list_tools()
        return {
            "mizani-mcp": [t.name for t in own_tools.tools],
            "mcp-server-duckdb": [t.name for t in borrowed_tools.tools],
        }

    async def run_borrowed_sql(self, sql: str) -> dict[str, Any]:
        """Execute one read-only SQL query via the borrowed DuckDB MCP.

        Tries common tool names used by mcp-server-duckdb variants.
        """
        assert self.borrowed is not None
        tools = await self.borrowed.list_tools()
        names = {t.name for t in tools.tools}
        # Prefer explicit query / execute tools.
        candidates = [
            ("query", {"sql": sql}),
            ("query", {"query": sql}),
            ("execute_sql", {"sql": sql}),
            ("execute_query", {"query": sql}),
            ("read_query", {"query": sql}),
        ]
        last_err: str | None = None
        for name, args in candidates:
            if name not in names:
                continue
            try:
                result = await self.borrowed.call_tool(name, args)
                payload = {
                    "tool": name,
                    "sql": sql,
                    "content": [
                        (c.text if hasattr(c, "text") else str(c))
                        for c in (result.content or [])
                    ],
                    "isError": bool(getattr(result, "isError", False)),
                }
                self.sql_results.append(payload)
                return payload
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
        # Fallback: first tool that accepts a string query-like arg.
        for t in tools.tools:
            for key in ("sql", "query", "q"):
                try:
                    result = await self.borrowed.call_tool(t.name, {key: sql})
                    payload = {
                        "tool": t.name,
                        "sql": sql,
                        "content": [
                            (c.text if hasattr(c, "text") else str(c))
                            for c in (result.content or [])
                        ],
                        "isError": bool(getattr(result, "isError", False)),
                    }
                    self.sql_results.append(payload)
                    return payload
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)
                    continue
        err = {
            "tool": None,
            "sql": sql,
            "error": last_err or f"no SQL tool among {sorted(names)}",
            "isError": True,
        }
        self.sql_results.append(err)
        return err


def borrowed_sql_sync(db_path: Path, root: Path, sql: str) -> dict[str, Any]:
    """Sync wrapper: open dual MCP briefly and run one borrowed SQL query.

    Used from LangGraph nodes that are not async. Domain detectors still run
    in-process; this exercises the borrowed MCP client path every run.
    """
    import asyncio

    async def _run() -> dict[str, Any]:
        async with DualMcpSession(db_path=db_path, root=root) as session:
            return await session.run_borrowed_sql(sql)

    return asyncio.run(_run())


def dual_mcp_probe_sync(db_path: Path, root: Path) -> dict[str, Any]:
    """List tools on both servers and run a count(*) awards SQL probe."""
    import asyncio

    async def _run() -> dict[str, Any]:
        async with DualMcpSession(db_path=db_path, root=root) as session:
            names = await session.list_tool_names()
            sql = await session.run_borrowed_sql(
                "SELECT count(*) AS awards_kept FROM awards WHERE NOT excluded"
            )
            return {
                "tool_names": names,
                "sql_probe": sql,
                "borrowed_sql_calls": len(session.sql_results),
            }

    return asyncio.run(_run())
