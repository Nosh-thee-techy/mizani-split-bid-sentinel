"""Unit tests for data-conduct filters."""

from mizani.ingest.filters import is_open_tender, looks_like_company, looks_like_person

MARKERS = ["ltd", "limited", "enterprises", "pharma", "medical"]


def test_open_tender_detected() -> None:
    assert is_open_tender("active", exclude_statuses=["planned", "active"])
    assert not is_open_tender("complete", exclude_statuses=["planned", "active"])


def test_company_marker() -> None:
    assert looks_like_company("MedSupplies Limited", MARKERS)
    assert not looks_like_company("Jane Wanjiku Otieno", MARKERS)


def test_person_like() -> None:
    assert looks_like_person(
        "Jane Wanjiku Otieno",
        company_markers=MARKERS,
        person_name_max_tokens=3,
    )
    assert not looks_like_person(
        "MedSupplies Limited",
        company_markers=MARKERS,
        person_name_max_tokens=3,
    )
