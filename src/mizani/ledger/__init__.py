"""Hash-chained evidence ledger: append-only, tamper-evident audit trail.

Public API used by the CLI and (later) the MCP tool wrapper.
"""

from mizani.ledger.chain import Ledger, LedgerError, logged_tool_call
from mizani.ledger.models import ApprovalPayload, LedgerEntry, LedgerEvent

__all__ = [
    "ApprovalPayload",
    "Ledger",
    "LedgerEntry",
    "LedgerError",
    "LedgerEvent",
    "logged_tool_call",
]
