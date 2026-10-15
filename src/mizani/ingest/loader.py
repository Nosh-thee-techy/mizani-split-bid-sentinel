"""Load OCDS release packages / releases from JSON and JSONL files.

Accepts:
  - A release package: {"releases": [...]}
  - A compiled release package / record package with releases
  - A bare release object
  - JSONL where each line is a package or release

Duplicate releases for the same OCID are resolved by keeping the latest
release date (or last-seen if dates are missing).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from mizani.ingest.filters import (
    is_open_tender,
    looks_like_person,
    party_role_is_buyer,
    party_role_is_supplier,
)
from mizani.ingest.models import AwardRow, BuyerRow, IngestStats, ItemRow, TenderRow


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an OCDS date-time string into a datetime, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    # Handle trailing Z
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Date-only
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def _parse_date(value: Any) -> date | None:
    """Parse an OCDS date or date-time into a date."""
    dt = _parse_datetime(value)
    return dt.date() if dt else None


def _as_float(value: Any) -> float | None:
    """Coerce OCDS amount fields to float."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _party_map(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index parties by id for buyer/supplier lookup."""
    out: dict[str, dict[str, Any]] = {}
    for party in release.get("parties") or []:
        pid = party.get("id")
        if pid:
            out[str(pid)] = party
    return out


def _buyer_from_release(release: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None]:
    """Extract buyer_id, buyer_name, county, region from a release.

    Prefers buyer object, then parties with buyer role.
    Location is never imputed — only read from address fields when present.
    """
    buyer = release.get("buyer") or {}
    buyer_id = str(buyer["id"]) if buyer.get("id") else None
    buyer_name = buyer.get("name")
    county = None
    region = None

    parties = _party_map(release)
    if buyer_id and buyer_id in parties:
        party = parties[buyer_id]
        buyer_name = buyer_name or party.get("name")
        address = party.get("address") or {}
        region = address.get("region") or address.get("locality")
        county = address.get("county") or address.get("region")
    else:
        for party in release.get("parties") or []:
            if party_role_is_buyer(party.get("roles")):
                buyer_id = buyer_id or (str(party["id"]) if party.get("id") else None)
                buyer_name = buyer_name or party.get("name")
                address = party.get("address") or {}
                region = region or address.get("region") or address.get("locality")
                county = county or address.get("county") or address.get("region")
                break

    # Tender procuringEntity fallback
    tender = release.get("tender") or {}
    pe = tender.get("procuringEntity") or {}
    if not buyer_id and pe.get("id"):
        buyer_id = str(pe["id"])
    if not buyer_name:
        buyer_name = pe.get("name")

    return buyer_id, buyer_name, county, region


def _suppliers_from_award(
    award: dict[str, Any],
    parties: dict[str, dict[str, Any]],
) -> list[tuple[str | None, str | None, str | None]]:
    """Return list of (raw_id, raw_name, id_scheme) for suppliers on an award.

    Names are recovered from award.suppliers[].name or the matching party name
    when the party record itself is missing a name (Kenya PPRA quirk).
    """
    results: list[tuple[str | None, str | None, str | None]] = []
    for supplier in award.get("suppliers") or []:
        sid = str(supplier["id"]) if supplier.get("id") else None
        name = supplier.get("name")
        scheme = None
        # identifier scheme when present (often invalid on Kenya data)
        ident = supplier.get("identifier") or {}
        if isinstance(ident, dict):
            scheme = ident.get("scheme")
            if not sid and ident.get("id"):
                sid = str(ident["id"])
            name = name or ident.get("legalName")
        if sid and sid in parties:
            party = parties[sid]
            name = name or party.get("name")
            pident = party.get("identifier") or {}
            if isinstance(pident, dict):
                scheme = scheme or pident.get("scheme")
        results.append((sid, name, scheme))
    if results:
        return results
    for party in parties.values():
        if party_role_is_supplier(party.get("roles")):
            sid = str(party["id"]) if party.get("id") else None
            scheme = None
            pident = party.get("identifier") or {}
            if isinstance(pident, dict):
                scheme = pident.get("scheme")
            results.append((sid, party.get("name"), scheme))
    return results


def _category_from_tender(tender: dict[str, Any]) -> str | None:
    """Best-effort category / item classification string."""
    items = tender.get("items") or []
    if items:
        classification = (items[0].get("classification") or {}) if isinstance(items[0], dict) else {}
        desc = classification.get("description") or items[0].get("description")
        if desc:
            return str(desc)[:200]
    main = tender.get("mainProcurementCategory") or tender.get("additionalProcurementCategories")
    if isinstance(main, list) and main:
        return str(main[0])
    if main:
        return str(main)
    return None


def _normalise_unit(unit: Any) -> str | None:
    """Extract a unit name/id string from an OCDS unit object or string."""
    if unit is None:
        return None
    if isinstance(unit, str):
        return unit.strip().lower() or None
    if isinstance(unit, dict):
        name = unit.get("name") or unit.get("id") or (unit.get("scheme") and unit.get("id"))
        if name:
            return str(name).strip().lower()
    return None


def _normalise_description(desc: str | None) -> str | None:
    """Lower-case collapsed description for peer grouping."""
    if not desc:
        return None
    return " ".join(desc.lower().split())


def iter_json_documents(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from a .json or .jsonl file."""
    # utf-8-sig strips a BOM if a Windows editor rewrote the file.
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
                if isinstance(doc, dict):
                    yield doc
                else:
                    raise ValueError(f"{path}:{line_no}: expected object, got {type(doc)}")
    else:
        with path.open(encoding="utf-8-sig") as handle:
            doc = json.load(handle)
        if isinstance(doc, list):
            for item in doc:
                if isinstance(item, dict):
                    yield item
        elif isinstance(doc, dict):
            yield doc
        else:
            raise ValueError(f"{path}: expected object or array")


def extract_releases(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull release objects from a package, record, or bare release."""
    if "releases" in doc and isinstance(doc["releases"], list):
        return [r for r in doc["releases"] if isinstance(r, dict)]
    if "compiledRelease" in doc and isinstance(doc["compiledRelease"], dict):
        return [doc["compiledRelease"]]
    # Bare release: has ocid and (tender or awards)
    if doc.get("ocid") and ("tender" in doc or "awards" in doc or doc.get("tag")):
        return [doc]
    return []


def discover_raw_files(raw_dir: Path) -> list[Path]:
    """List JSON/JSONL files under raw_dir (non-recursive and one level deep)."""
    if not raw_dir.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.json", "*.jsonl"):
        files.extend(sorted(raw_dir.glob(pattern)))
        files.extend(sorted(raw_dir.glob(f"*/{pattern}")))
    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in files:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def load_latest_releases(raw_dir: Path, stats: IngestStats) -> dict[str, dict[str, Any]]:
    """Load all releases and keep the latest per OCID.

    Inputs:
        raw_dir: directory containing OCDS JSON/JSONL.
        stats: mutable ingest counters.
    Outputs:
        Mapping ocid -> chosen release dict.
    """
    by_ocid: dict[str, dict[str, Any]] = {}
    by_ocid_date: dict[str, datetime | None] = {}

    for path in discover_raw_files(raw_dir):
        for doc in iter_json_documents(path):
            for release in extract_releases(doc):
                stats.releases_seen += 1
                ocid = release.get("ocid")
                if not ocid:
                    stats.add_note(f"Skipped release without ocid in {path.name}")
                    continue
                ocid_s = str(ocid)
                release_date = _parse_datetime(release.get("date"))
                existing_date = by_ocid_date.get(ocid_s)
                if ocid_s in by_ocid:
                    # Keep newer; if equal/missing, keep the later-seen copy.
                    if existing_date and release_date and release_date < existing_date:
                        stats.duplicate_releases_dropped += 1
                        continue
                    stats.duplicate_releases_dropped += 1
                by_ocid[ocid_s] = release
                by_ocid_date[ocid_s] = release_date

    stats.releases_kept = len(by_ocid)
    return by_ocid


def release_to_rows(
    release: dict[str, Any],
    *,
    exclude_open_statuses: list[str],
    company_markers: list[str],
    person_name_max_tokens: int,
    exclude_person_like: bool,
    keep_awarded_when_unknown: bool,
    stats: IngestStats,
) -> tuple[TenderRow | None, list[AwardRow], list[ItemRow], BuyerRow | None]:
    """Convert one OCDS release into normalised tender/award/item/buyer rows.

    Open tenders are marked excluded and their awards are dropped.
    Person-like suppliers are dropped from awards when configured.
    """
    ocid = str(release.get("ocid"))
    release_id = str(release["id"]) if release.get("id") else None
    release_date = _parse_datetime(release.get("date"))
    tender = release.get("tender") or {}
    buyer_id, buyer_name, county, region = _buyer_from_release(release)
    parties = _party_map(release)
    category = _category_from_tender(tender)

    tender_status = tender.get("status")
    open_flag = is_open_tender(tender_status, exclude_statuses=exclude_open_statuses)
    tender_value = (tender.get("value") or {}) if isinstance(tender.get("value"), dict) else {}
    estimate = _as_float(tender_value.get("amount"))
    estimate_currency = tender_value.get("currency")

    tender_row: TenderRow | None = None
    if tender:
        stats.tenders_raw += 1
        exclude_reason = "open_tender" if open_flag else None
        if open_flag:
            stats.tenders_excluded_open += 1
        tender_id = str(tender.get("id") or ocid)
        tender_row = TenderRow(
            ocid=ocid,
            tender_id=tender_id,
            record_id=f"ocds-tender-{ocid}-{tender_id}",
            title=tender.get("title"),
            description=tender.get("description"),
            status=tender_status,
            procurement_method=tender.get("procurementMethod")
            or tender.get("procurementMethodDetails"),
            category=category,
            value_amount=estimate,
            value_currency=estimate_currency,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            county=county,
            region=region,
            tender_period_end=_parse_date((tender.get("tenderPeriod") or {}).get("endDate")),
            date_published=_parse_date(tender.get("datePublished") or release.get("date")),
            release_id=release_id,
            release_date=release_date,
            is_open=open_flag,
            excluded=open_flag,
            exclude_reason=exclude_reason,
        )
        if not open_flag:
            stats.tenders_kept += 1

    buyer_row: BuyerRow | None = None
    if buyer_id:
        buyer_row = BuyerRow(
            buyer_id=buyer_id,
            name=buyer_name,
            county=county,
            region=region,
        )
        if not buyer_name:
            stats.missing_buyer_name += 1
        if not county and not region:
            stats.missing_location += 1

    awards_out: list[AwardRow] = []
    items_out: list[ItemRow] = []

    # If tender is open, drop awards entirely (live competition).
    if open_flag:
        for _ in release.get("awards") or []:
            stats.awards_raw += 1
            stats.awards_excluded_open_tender += 1
        return tender_row, awards_out, items_out, buyer_row

    # Unknown tender status with no awards: nothing to keep on award side.
    awards = release.get("awards") or []
    if not awards:
        stats.releases_with_empty_awards += 1
    if not awards and tender_status is None and not keep_awarded_when_unknown:
        return tender_row, awards_out, items_out, buyer_row

    procurement_method = tender.get("procurementMethod") or tender.get(
        "procurementMethodDetails"
    )

    for award in awards:
        stats.awards_raw += 1
        award_id = str(award.get("id") or f"unknown-{stats.awards_raw}")
        value = (award.get("value") or {}) if isinstance(award.get("value"), dict) else {}
        amount = _as_float(value.get("amount"))
        currency = value.get("currency")
        if currency and currency not in stats.currency_set:
            stats.currency_set.append(str(currency))

        suppliers = _suppliers_from_award(award, parties)
        if not suppliers:
            suppliers = [(None, None, None)]

        for raw_id, raw_name, id_scheme in suppliers:
            caveats: list[str] = []
            excluded = False
            exclude_reason: str | None = None

            if exclude_person_like and raw_name and looks_like_person(
                raw_name,
                company_markers=company_markers,
                person_name_max_tokens=person_name_max_tokens,
            ):
                excluded = True
                exclude_reason = "person_like_supplier"
                stats.awards_excluded_person_like += 1

            if not buyer_name:
                caveats.append("buyer name missing")
            if not county and not region:
                caveats.append("location missing")
            if raw_id is None:
                caveats.append("supplier id missing")
            # Kenya quirk: identifier schemes are often missing or non-standard.
            if id_scheme in (None, "", "null", "None"):
                caveats.append("supplier id scheme missing or invalid")
                stats.invalid_supplier_id_schemes += 1
            elif str(id_scheme).lower() in {"unknown", "n/a", "na", "none"}:
                caveats.append(f"supplier id scheme invalid: {id_scheme}")
                stats.invalid_supplier_id_schemes += 1

            record_id = f"ocds-award-{ocid}-{award_id}"
            if raw_id:
                record_id = f"{record_id}-{raw_id}"

            row = AwardRow(
                ocid=ocid,
                award_id=award_id,
                record_id=record_id,
                title=award.get("title") or tender.get("title"),
                status=award.get("status"),
                date=_parse_date(award.get("date")),
                value_amount=amount,
                value_currency=currency,
                buyer_id=buyer_id,
                buyer_name=buyer_name,
                supplier_raw_id=raw_id,
                supplier_raw_name=raw_name,
                category=category,
                procurement_method=str(procurement_method) if procurement_method else None,
                county=county,
                region=region,
                tender_estimate=estimate,
                tender_estimate_currency=estimate_currency,
                supplier_id_scheme=str(id_scheme) if id_scheme else None,
                release_id=release_id,
                release_date=release_date,
                excluded=excluded,
                exclude_reason=exclude_reason,
                data_caveats=caveats,
            )
            if not excluded:
                stats.awards_kept += 1
            awards_out.append(row)

            # Items: prefer award.items, else tender.items attached to this award
            source_items = award.get("items") or tender.get("items") or []
            for idx, item in enumerate(source_items):
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or idx)
                unit_value = item.get("unit") or {}
                unit_amount = None
                unit_currency = currency
                if isinstance(unit_value, dict):
                    uv = unit_value.get("value") or {}
                    if isinstance(uv, dict):
                        unit_amount = _as_float(uv.get("amount"))
                        unit_currency = uv.get("currency") or currency
                quantity = _as_float(item.get("quantity"))
                # Derive unit price from total/quantity when unit value absent
                total = amount
                if unit_amount is None and total is not None and quantity and quantity > 0:
                    unit_amount = total / quantity

                items_out.append(
                    ItemRow(
                        ocid=ocid,
                        award_id=award_id,
                        record_id=f"ocds-item-{ocid}-{award_id}-{item_id}",
                        item_id=item_id,
                        description=item.get("description"),
                        description_norm=_normalise_description(item.get("description")),
                        quantity=quantity,
                        unit=_normalise_unit(item.get("unit")),
                        unit_norm=_normalise_unit(item.get("unit")),
                        unit_value_amount=unit_amount,
                        unit_value_currency=unit_currency,
                        total_value_amount=total,
                        buyer_id=buyer_id,
                        category=category,
                        award_date=_parse_date(award.get("date")),
                        excluded=excluded,
                        exclude_reason=exclude_reason,
                    )
                )
                if not excluded:
                    stats.items_kept += 1

    return tender_row, awards_out, items_out, buyer_row
