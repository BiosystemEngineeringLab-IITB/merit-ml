#!/usr/bin/env python3
"""Figure: sample counts per analysis (before vs after QC filter) for three sources."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

matplotlib.rc("font", family="DejaVu Sans")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"]  = 42

OUT_DIR  = Path("/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-3")
DATA_FILE = Path("/tmp/sample_counts.json")

# ── colours ───────────────────────────────────────────────────────────────────
C_RAW  = "#5b8db8"   # steel blue  – before QC
C_FILT = "#e07b54"   # terracotta  – after QC

# ── load data ─────────────────────────────────────────────────────────────────
with open(DATA_FILE) as f:
    d = json.load(f)

groups = [
    # (label, raw_list, filt_list)
    ("datatable",   d["dt_raw"], d["dt_filt"]),
    ("mwtab",       d["mw_raw"], d["mw_filt"]),
    ("untarg_data", d["ut_raw"], d["ut_filt"]),
]

# ── layout helpers ─────────────────────────────────────────────────────────────
def styled_bp(ax, data, pos, color, flier_size=2.5):
    bp = ax.boxplot(
        data, positions=[pos], widths=0.35,
        patch_artist=True, showfliers=True,
        flierprops=dict(marker="o", markersize=flier_size,
                        markerfacecolor=color, markeredgecolor=color, alpha=0.35),
        medianprops=dict(color="white", linewidth=2.0),
        boxprops=dict(facecolor=color, linewidth=1.2),
        whiskerprops=dict(color=color, linewidth=1.2),
        capprops=dict(color=color, linewidth=1.5),
    )
    return bp

def annotate_median(ax, val, pos, color, offset_frac=0.06, xmax=None):
    ax.text(pos, val * (1 + offset_frac), f"{int(val)}",
            ha="center", va="bottom",
            fontsize=9, fontweight="bold", color=color)

# ── build figure ──────────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(13, 5.5), sharey=False)
fig.subplots_adjust(left=0.07, right=0.97, top=0.88, bottom=0.13, wspace=0.38)

tick_positions = []   # for x-axis

for ax, (label, raw, filt) in zip(axes, groups):
    has_filt = filt is not None

    pos_raw, pos_filt = 1.0, 2.0
    styled_bp(ax, raw,  pos_raw,  C_RAW)
    styled_bp(ax, filt, pos_filt, C_FILT)
    ax.set_xticks([pos_raw, pos_filt])
    ax.set_xticklabels(["Before\nQC filter", "After\nQC filter"],
                       fontsize=10, fontweight="bold")
    med_raw  = float(np.median(raw))
    med_filt = float(np.median(filt))
    annotate_median(ax, med_raw,  pos_raw,  C_RAW)
    annotate_median(ax, med_filt, pos_filt, C_FILT)

    # log y-axis
    ax.set_yscale("log")
    ax.set_ylim(bottom=0.8)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda x, _: f"{int(x):,}" if x >= 1 else f"{x:.1f}"
    ))

    # labels
    ax.set_title(label, fontsize=14, fontweight="bold", pad=8)
    ax.set_ylabel("Samples per analysis" if ax is axes[0] else "",
                  fontsize=12, fontweight="bold")
    ax.tick_params(axis="both", labelsize=11, width=1.2)
    for spine in ax.spines.values():
        spine.set_linewidth(1.2)

    # n= annotation
    n_str = f"n = {len(raw):,}"
    ax.text(0.97, 0.01, n_str, transform=ax.transAxes,
            ha="right", va="bottom", fontsize=9, color="#555555")

# ── legend ────────────────────────────────────────────────────────────────────
patch_raw  = mpatches.Patch(facecolor=C_RAW,  label="Before QC filter")
patch_filt = mpatches.Patch(facecolor=C_FILT, label="After QC filter")
fig.legend(handles=[patch_raw, patch_filt],
           loc="upper center", ncol=2, fontsize=11,
           frameon=False, bbox_to_anchor=(0.5, 0.99),
           prop={"weight": "bold", "size": 11})

# ── caption note ──────────────────────────────────────────────────────────────
fig.text(0.5, 0.005,
         "Y-axis: log scale.  Medians annotated.  "
         "mwtab: QC filter applied via Samples (row 0) and Factors (row 1) of the metabolite data block.",
         ha="center", va="bottom", fontsize=8, color="#666666", style="italic")

# ── save ──────────────────────────────────────────────────────────────────────
for ext in ("pdf", "svg", "png"):
    out = OUT_DIR / f"figure_sample_counts_boxplot.{ext}"
    fig.savefig(out, dpi=300 if ext == "png" else None, bbox_inches="tight")
    print(f"Saved: {out}")

plt.close(fig)
