"""Unit tests for supplier canonicalisation."""

from mizani.ingest.canonicalise import SupplierCanonicaliser, normalise_name


def test_normalise_strips_ltd() -> None:
    assert normalise_name("MedSupplies Limited") == "medsupplies"
    assert normalise_name("Med Supplies Ltd") == "med supplies"


def test_fuzzy_merge_variants() -> None:
    canon = SupplierCanonicaliser(fuzzy_threshold=90.0)
    k1 = canon.observe("sup-a", "MedSupplies Limited")
    k2 = canon.observe("sup-b", "MEDSUPPLIES LIMITED")
    k3 = canon.observe(None, "Med Supplies Ltd")
    assert k1 == k2 == k3
    result = canon.result()
    assert len(result.suppliers) == 1
