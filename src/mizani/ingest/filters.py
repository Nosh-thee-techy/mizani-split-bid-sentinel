"""Data-conduct filters: exclude live tenders and person-like suppliers.

These rules implement the challenge constraint that Mizani must never touch
a live tender or personal details — company and buyer records only.
"""

from __future__ import annotations

import re
from typing import Any

# Word-boundary check for company markers in supplier names.
_WORD = re.compile(r"[a-z0-9&.'-]+", re.IGNORECASE)


def normalise_status(status: str | None) -> str | None:
    """Lower-case and strip a tender/award status string."""
    if status is None:
        return None
    cleaned = status.strip().lower()
    return cleaned or None


def is_open_tender(
    status: str | None,
    *,
    exclude_statuses: list[str],
) -> bool:
    """Return True when the tender status is treated as still live/open.

    Inputs:
        status: OCDS tender.status (or equivalent).
        exclude_statuses: configured open statuses from ingest.yaml.
    Outputs:
        True if this tender must be dropped from analysis.
    """
    normalised = normalise_status(status)
    if normalised is None:
        return False
    excluded = {s.strip().lower() for s in exclude_statuses}
    return normalised in excluded


def looks_like_company(name: str, company_markers: list[str]) -> bool:
    """Heuristic: True if the name contains a common company/entity marker.

    Inputs:
        name: raw supplier name.
        company_markers: tokens from ingest.yaml (ltd, limited, …).
    Outputs:
        True when a marker token appears as a whole word.
    """
    tokens = {t.lower() for t in _WORD.findall(name)}
    markers = {m.lower().rstrip(".") for m in company_markers}
    # Also allow "co" from "co."
    markers |= {m.rstrip(".") for m in markers}
    return bool(tokens & markers)


def looks_like_person(
    name: str,
    *,
    company_markers: list[str],
    person_name_max_tokens: int,
) -> bool:
    """Conservative person heuristic for data-conduct filtering.

    Inputs:
        name: raw supplier name.
        company_markers: if any match, treat as company (not a person).
        person_name_max_tokens: names with this many alphabetic tokens or fewer
            and no company marker are treated as person-like.
    Outputs:
        True when the record should be excluded as personal data.
    Why:
        OCDS awards sometimes list individual contractors. Mizani must not
        profile individuals, so we drop these rather than analyse them.
    """
    cleaned = name.strip()
    if not cleaned:
        return False
    if looks_like_company(cleaned, company_markers):
        return False
    # Strip common titles, then count name tokens.
    without_titles = re.sub(
        r"\b(mr|mrs|ms|miss|dr|eng|hon|prof)\b\.?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    tokens = [t for t in _WORD.findall(without_titles) if not t.isdigit()]
    if not tokens:
        return False
    # Registration-like codes (e.g. PVT-xxx) are not persons.
    if any(re.search(r"\d", t) for t in tokens):
        return False
    return len(tokens) <= person_name_max_tokens


def party_role_is_buyer(roles: Any) -> bool:
    """True if an OCDS party roles list includes buyer / procuringEntity."""
    if not roles:
        return False
    if isinstance(roles, str):
        roles = [roles]
    normalised = {str(r).lower() for r in roles}
    return bool(normalised & {"buyer", "procuringentity", "procuring entity"})


def party_role_is_supplier(roles: Any) -> bool:
    """True if an OCDS party roles list includes supplier / tenderer."""
    if not roles:
        return False
    if isinstance(roles, str):
        roles = [roles]
    normalised = {str(r).lower() for r in roles}
    return bool(normalised & {"supplier", "tenderer"})
