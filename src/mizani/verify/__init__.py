"""Independent finding verification.

Why independence matters: if verification called the same detector functions,
a bug in the detector would be confirmed by itself. Reloading raw records by
ID and recomputing metrics on a separate code path is the only way a DROPPED
status is trustworthy to a committee.
"""

from mizani.verify.engine import verify_finding

__all__ = ["verify_finding"]
