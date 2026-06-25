#!/usr/bin/env python3
"""Feature count violin+boxplot for all three MW sources (raw counts, no deduplication)."""
from __future__ import annotations

import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from scipy.stats import gaussian_kde
from pathlib import Path
from statistics import median

matplotlib.rc("font", family="DejaVu Sans")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"]  = 42

OUT_DIR  = Path("/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-3")
DATA_FILE = Path("/tmp/feat_counts_raw.json")

# ── colours ──────────────────────────────────────────────────────────────────
COLOURS = {
    "Datatable":   "#5b9bd5",
    "mwTab":       "#70ad47",
    "Untarg data": "#f5a623",
}

# ── load data ─────────────────────────────────────────────────────────────────
with open(DATA_FILE) as f:
    raw = json.load(f)

# mwtab: drop 0-feature entries (header-only blocks, no valid data rows)
groups = {
    "Datatable":   [r["n"] for r in raw["dt"] if r["n"] > 0],
    "mwTab":       [r["n"] for r in raw["mw"] if r["n"] > 0],
    "Untarg data": [r["n"] for r in raw["ut"] if r["n"] > 0],
}

# ── log-space KDE violin ──────────────────────────────────────────────────────
def draw_violin(ax, data, pos, color, width=0.38):
    log_data = np.log10(data)
    kde = gaussian_kde(log_data, bw_method=0.25)
    y_log = np.linspace(log_data.min(), log_data.max(), 400)
    density = kde(y_log)
    density = density / density.max() * width
    y_orig = 10 ** y_log
    ax.fill_betweenx(y_orig, pos - density, pos + density,
                     color=color, alpha=0.72, linewidth=0)
    ax.plot(pos - density, y_orig, color=color, lw=0.6, alpha=0.5)
    ax.plot(pos + density, y_orig, color=color, lw=0.6, alpha=0.5)


def draw_boxplot(ax, data, pos, color):
    q1, med, q3 = np.percentile(data, [25, 50, 75])
    iqr = q3 - q1
    lo = max(min(data), q1 - 1.5 * iqr)
    hi = min(max(data), q3 + 1.5 * iqr)
    box_w = 0.10
    # box
    rect = plt.Rectangle((pos - box_w / 2, q1), box_w, q3 - q1,
                          facecolor="white", edgecolor="#333333", lw=1.4, zorder=4)
    ax.add_patch(rect)
    # median line
    ax.plot([pos - box_w / 2, pos + box_w / 2], [med, med],
            color="#222222", lw=2.0, zorder=5)
    # whiskers
    ax.plot([pos, pos], [lo, q1], color="#444444", lw=1.2, zorder=3)
    ax.plot([pos, pos], [q3, hi], color="#444444", lw=1.2, zorder=3)
    # whisker caps
    cap_w = 0.05
    for y in (lo, hi):
        ax.plot([pos - cap_w, pos + cap_w], [y, y],
                color="#444444", lw=1.2, zorder=3)


# ── figure ────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(7, 5.5))

positions = {"Datatable": 1, "mwTab": 2, "Untarg data": 3}

for label, data in groups.items():
    pos   = positions[label]
    color = COLOURS[label]
    draw_violin(ax, data, pos, color)
    draw_boxplot(ax, data, pos, color)
    med = median(data)
    # median annotation — above the violin
    y_ann = np.percentile(data, 99) * 1.6
    ax.text(pos, y_ann, f"med={int(med):,}",
            ha="center", va="bottom", fontsize=10,
            fontweight="bold", color="#222222")

# ── axes ──────────────────────────────────────────────────────────────────────
ax.set_yscale("log")
ax.set_ylim(0.8, 8e5)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(
    lambda x, _: f"{int(x):,}" if x >= 1 else f"{x:.1f}"
))
ax.set_xlim(0.4, 3.6)
ax.set_xticks([1, 2, 3])
ax.set_xticklabels(list(groups.keys()),
                   fontsize=12, fontweight="bold")
ax.tick_params(axis="y", labelsize=11, which="both")
for lbl in ax.get_yticklabels():
    lbl.set_fontweight("bold")

ax.set_ylabel("Feature count (raw)", fontsize=12, fontweight="bold")
ax.set_title("Feature count per analysis across MW sources\n(raw column/row counts, no deduplication)",
             fontsize=14, fontweight="bold", pad=10)

ax.yaxis.grid(True, which="major", linestyle="--", linewidth=0.6,
              color="#cccccc", zorder=0)
ax.set_axisbelow(True)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

# n= annotation at bottom
for label, data in groups.items():
    pos = positions[label]
    ax.text(pos, 0.95, f"n={len(data):,}",
            ha="center", va="top", fontsize=9,
            color="#555555", transform=ax.get_xaxis_transform())

fig.tight_layout()

for ext in ("pdf", "svg", "png"):
    out = OUT_DIR / f"figure_feature_violin.{ext}"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    print(f"Saved: {out}")

plt.close(fig)
