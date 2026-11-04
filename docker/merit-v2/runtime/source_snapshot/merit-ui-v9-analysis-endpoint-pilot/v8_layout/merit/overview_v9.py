"""Overview of one study (the Overview tab of the v9 pilot): no parameters of its own.

Decided 2026-10-20 (Mockup A): one grid, rows = analyses; column groups Structural and Analytical QC (one group of tiles per endpoint,
one tile per scored item) and Metadata & FAIR (one group per analysis). Each tile shows the value its own tab shows; continuous items as
a continuous colour (no thresholds), 0/100 items as a tick or cross, the LC-method checks as three pips; nothing is combined, averaged
or ranked. The rendering is in merit/pilot_analysis_endpoint.py (overview_scores, overview_right_column, export_overview_block).
"""
from __future__ import annotations

PARAMETERS: dict[str, tuple[float, float, float, float]] = {}
DEFAULT_PARAMS: dict[str, float] = {}
