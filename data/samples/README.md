# Sample OCDS data (synthetic)

`synthetic_kenya_shaped.jsonl` is **synthetic**, labelled for development and
demos of the ingest pipeline. It is shaped like Kenya PPRA/PPIP OCDS release
packages but is **not** mixed into real-data findings.

It exercises:

- open tender exclusion
- person-like supplier exclusion
- duplicate releases (same OCID, newer wins)
- missing buyer name
- missing location
- supplier name/id variants that should merge
- extreme value outlier
- closed awards suitable for later detectors
