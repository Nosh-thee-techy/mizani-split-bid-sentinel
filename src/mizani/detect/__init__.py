"""Deterministic detectors: split, price, concentration, gap.

The model never calls these maths paths directly for numbers in briefs —
MCP tools wrap them and the verifier recomputes independently.
"""

from mizani.detect.concentration import detect_concentration
from mizani.detect.gap import detect_award_estimate_gaps
from mizani.detect.price import detect_price_outliers
from mizani.detect.split import detect_splits

__all__ = [
    "detect_award_estimate_gaps",
    "detect_concentration",
    "detect_price_outliers",
    "detect_splits",
]
