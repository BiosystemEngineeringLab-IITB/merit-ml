"""Measurement scale of one matrix (one analysis from one endpoint), Analytical QC.

Scored (one item):
  units declared   100 when a valid UNITS declaration exists, else 0. A valid declaration is a non-empty, non-placeholder text
                   (the manuscript's rule, Text S15: missing or empty field, NA, not applicable, '-' or a number alone = not declared).
                   mwTab and datatable: the UNITS of the mwTab quantitative data block of the analysis (the datatable is read
                   with the same declaration). untarg_data: the UNITS of the mwTab line naming the results file; the data-block
                   declaration is not used for untarg_data (Text S15).
Information, not scored (the manuscript reports scale as a descriptor and does not infer or verify scale from it):
  deposited scale  median of all non-zero values of the matrix (negative values included; Table 4, Table S20a) and their Q1-Q3,
                   share of the non-zero values that are negative, 5th-95th percentile of the positive values (orders of magnitude)
  declared family  intensity/area/counts, concentration/amount, normalised/relative/ratio, log indicated, other or mixed (Tables S20b-S20D)
  datatable        share of the paired non-zero mwTab numbers that the datatable shows unchanged (to its printed precision)
  samples          spread of the per-sample medians of the ML-eligible samples (orders of magnitude between their 5th and 95th
                   percentile, positive medians only) and samples whose median is more than 10x from the median sample
A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from merit import structural_v9 as sv

PARAMETERS: dict[str, tuple[float, float, float, float]] = {}
DEFAULT_PARAMS: dict[str, float] = {}


def _q(values: list[float], f: float) -> float:
    v = sorted(values)
    k = (len(v) - 1) * f
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def samples(roster: list[list], sample_medians: list[float | None], overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Per-sample medians of the ML-eligible samples (after the sample edits in force)."""
    meds = [m for p, m in zip(sv.effective_positions(roster, overrides), sample_medians)
            if not p["row"][sv.I_ALIGN] and not p["excluded"] and p["eligible"] and m is not None]
    pos = [m for m in meds if m > 0]
    out: dict[str, Any] = {"ml_eligible_with_a_median": len(meds), "positive_medians": len(pos)}
    if len(pos) >= 2:
        typical = statistics.median(pos)
        out["orders_of_magnitude_p5_p95"] = math.log10(_q(pos, .95) / _q(pos, .05))
        out["median_sample"] = typical
        out["more_than_10x_from_median_sample"] = sum(1 for m in pos if abs(math.log10(m / typical)) > 1)
    return out


def score(declared: bool) -> float:
    return 100.0 if declared else 0.0
