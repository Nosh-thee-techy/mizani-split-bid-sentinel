"""Human approval / rejection — NOT an MCP tool and not imported by the agent.

Only this module (via CLI) may change a brief from DRAFT to APPROVED/REJECTED.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from mizani.ledger import Ledger
from mizani.ledger.models import ApprovalPayload, LedgerActor, LedgerEntry, LedgerEvent


class ApprovalError(Exception):
    """Raised when approval/rejection cannot proceed."""


def _meta_path(brief_path: Path) -> Path:
    return brief_path.with_suffix(".json")


def show_brief(brief_path: Path) -> str:
    """Return brief markdown contents."""
    return Path(brief_path).read_text(encoding="utf-8")


def approve_brief(
    *,
    brief_path: Path,
    approver: str,
    note: str,
    ledger_path: Path,
    release_dir: Path,
) -> dict:
    """Approve a DRAFT brief under a named human and copy to release_dir."""
    approver = approver.strip()
    if not approver:
        raise ApprovalError("approver name must be non-empty")
    brief_path = Path(brief_path)
    meta_path = _meta_path(brief_path)
    if not brief_path.exists() or not meta_path.exists():
        raise ApprovalError(f"brief or meta missing: {brief_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") != "DRAFT":
        raise ApprovalError(f"brief status is {meta.get('status')}, expected DRAFT")
    body = brief_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != meta.get("content_hash"):
        raise ApprovalError("brief content hash mismatch — file may have been edited")

    meta["status"] = "APPROVED"
    meta["approver"] = approver
    meta["note"] = note
    # Rewrite markdown status line
    body2 = body.replace("**Status:** DRAFT", "**Status:** APPROVED", 1)
    brief_path.write_text(body2, encoding="utf-8")
    meta["content_hash"] = hashlib.sha256(body2.encode("utf-8")).hexdigest()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    release_dir = Path(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    released = release_dir / brief_path.name
    shutil.copy2(brief_path, released)
    shutil.copy2(meta_path, release_dir / meta_path.name)

    ledger = Ledger(ledger_path)
    ledger.append(
        LedgerEntry(
            run_id=meta.get("run_id") or "run_unknown",
            actor=LedgerActor.HUMAN,
            event=LedgerEvent.APPROVAL,
            tool=None,
            inputs={"brief_id": meta.get("brief_id")},
            outputs_digest=meta["content_hash"],
            record_ids=meta.get("finding_ids") or ["brief:approved"],
            approval=ApprovalPayload(approver=approver, decision="approve", note=note),
        )
    )
    return meta


def reject_brief(
    *,
    brief_path: Path,
    approver: str,
    note: str,
    ledger_path: Path,
) -> dict:
    """Reject a DRAFT brief under a named human."""
    approver = approver.strip()
    if not approver:
        raise ApprovalError("approver name must be non-empty")
    brief_path = Path(brief_path)
    meta_path = _meta_path(brief_path)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") != "DRAFT":
        raise ApprovalError(f"brief status is {meta.get('status')}, expected DRAFT")
    meta["status"] = "REJECTED"
    meta["approver"] = approver
    meta["note"] = note
    body = brief_path.read_text(encoding="utf-8").replace(
        "**Status:** DRAFT", "**Status:** REJECTED", 1
    )
    brief_path.write_text(body, encoding="utf-8")
    meta["content_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    ledger = Ledger(ledger_path)
    ledger.append(
        LedgerEntry(
            run_id=meta.get("run_id") or "run_unknown",
            actor=LedgerActor.HUMAN,
            event=LedgerEvent.REJECTION,
            inputs={"brief_id": meta.get("brief_id")},
            outputs_digest=meta["content_hash"],
            record_ids=meta.get("finding_ids") or ["brief:rejected"],
            approval=ApprovalPayload(approver=approver, decision="reject", note=note),
        )
    )
    return meta
