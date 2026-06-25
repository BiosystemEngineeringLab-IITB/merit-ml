#!/usr/bin/env python3
"""
Violin + boxplot figures for sample counts across three MW sources.

Main text  : one figure, three sources (after QC filter)
Supplementary: three panels, before vs after QC filter per source
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from scipy.stats import gaussian_kde

matplotlib.rc("font", family="DejaVu Sans")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"]  = 42

DATA_FILE = Path("/tmp/sample_counts.json")
OUT_DIR   = Path("/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-3")

# ── palette ───────────────────────────────────────────────────────────────────
COLORS = {
    "datatable":   "#5b8db8",   # steel blue
    "mwtab":       "#6aab72",   # muted green
    "untarg_data": "#e8a945",   # amber
}
COLORS_LIGHT = {k: v + "55" for k, v in COLORS.items()}   # 33% alpha hex

# ── load data ─────────────────────────────────────────────────────────────────
with open(DATA_FILE) as f:
    d = json.load(f)

sources = ["datatable", "mwtab", "untarg_data"]
labels  = ["Datatable", "mwTab", "Untarg data"]
raw  = {"datatable": d["dt_raw"],  "mwtab": d["mw_raw"],  "untarg_data": d["ut_raw"]}
filt = {"datatable": d["dt_filt"], "mwtab": d["mw_filt"], "untarg_data": d["ut_filt"]}


# ── core drawing helpers ───────────────────────────────────────────────────────
def log_violin(ax, data, pos, color, half_width=0.38, alpha=0.72, bw=0.25):
    """KDE computed in log10 space; plotted on a log y-axis."""
    vals = np.array([x for x in data if x > 0], dtype=float)
    log_vals = np.log10(vals)
    kde  = gaussian_kde(log_vals, bw_method=bw)
    y_log = np.linspace(log_vals.min(), log_vals.max(), 400)
    dens  = kde(y_log)
    dens  = dens / dens.max() * half_width
    y_lin = 10 ** y_log
    ax.fill_betweenx(y_lin, pos - dens, pos + dens,
                     color=color, alpha=alpha, linewidth=0)
    ax.plot(pos - dens, y_lin, color=color, lw=0.6, alpha=0.6)
    ax.plot(pos + dens, y_lin, color=color, lw=0.6, alpha=0.6)


def embed_box(ax, data, pos, box_width=0.13):
    """White-face boxplot overlaid on violin."""
    vals = [x for x in data if x > 0]
    bp = ax.boxplot(
        vals, positions=[pos], widths=box_width,
        patch_artist=True, showfliers=False,
        medianprops=dict(color="black", linewidth=2.0),
        boxprops=dict(facecolor="white", linewidth=1.2),
        whiskerprops=dict(color="#333333", linewidth=1.2),
        capprops=dict(color="#333333", linewidth=1.4),
    )
    return float(np.median(vals))


def style_log_ax(ax, ylabel=True):
    ax.set_yscale("log")
    ax.set_ylim(0.8, 2e4)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda x, _: (f"{int(x):,}" if x >= 1 else f"{x:.1f}")
    ))
    ax.yaxis.grid(True, which="major", linestyle="--", linewidth=0.6,
                  color="#cccccc", zorder=0)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel("Sample count", fontsize=12, fontweight="bold")
    ax.tick_params(axis="both", labelsize=11, width=1.2)
    for sp in ax.spines.values():
        sp.set_linewidth(1.2)


# ══════════════════════════════════════════════════════════════════════════════
# FIGURE 1 — main text: three sources, after QC filter
# ══════════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(7, 5.5))
fig.subplots_adjust(left=0.13, right=0.95, top=0.91, bottom=0.10)

positions = [1, 2, 3]
for pos, src, lbl in zip(positions, sources, labels):
    color = COLORS[src]
    data  = filt[src]
    log_violin(ax, data, pos, color)
    med = embed_box(ax, data, pos)
    # median annotation
    ax.text(pos, ax.get_ylim()[1] * 0.55,
            f"med={int(med)}",
            ha="center", va="bottom", fontsize=10,
            fontweight="bold", color="#333333")

style_log_ax(ax)
ax.set_xticks(positions)
ax.set_xticklabels(labels, fontsize=12, fontweight="bold")
ax.set_title("Sample count per analysis across MW sources\n(after QC/blank exclusion)",
             fontsize=13, fontweight="bold", pad=8)
ax.text(0.98, 0.01,
        f"datatable n={len(filt['datatable']):,}   "
        f"mwtab n={len(filt['mwtab']):,}   "
        f"untarg n={len(filt['untarg_data']):,}",
        transform=ax.transAxes, ha="right", va="bottom",
        fontsize=8, color="#666666")

for ext in ("pdf", "svg", "png"):
    p = OUT_DIR / f"figure_sample_violin_main.{ext}"
    fig.savefig(p, dpi=300 if ext == "png" else None, bbox_inches="tight")
    print(f"Saved: {p}")
plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# FIGURE 2 — supplementary: before vs after per source, 3 panels
# ══════════════════════════════════════════════════════════════════════════════
fig2, axes = plt.subplots(1, 3, figsize=(13, 5.5), sharey=True)
fig2.subplots_adjust(left=0.08, right=0.97, top=0.88, bottom=0.11, wspace=0.08)

for ax2, src, lbl in zip(axes, sources, labels):
    color = COLORS[src]
    r_data = raw[src]
    f_data = filt[src]

    # before: lighter shade
    log_violin(ax2, r_data, 1.0, color, alpha=0.40)
    med_r = embed_box(ax2, r_data, 1.0)

    # after: full colour
    log_violin(ax2, f_data, 2.0, color, alpha=0.80)
    med_f = embed_box(ax2, f_data, 2.0)

    # median annotations
    y_top = 1.2e4
    ax2.text(1.0, y_top, f"med={int(med_r)}", ha="center", va="bottom",
             fontsize=10, fontweight="bold", color="#555555")
    ax2.text(2.0, y_top, f"med={int(med_f)}", ha="center", va="bottom",
             fontsize=10, fontweight="bold", color=color)

    style_log_ax(ax2, ylabel=(ax2 is axes[0]))
    ax2.set_xticks([1.0, 2.0])
    ax2.set_xticklabels(["Before\nQC filter", "After\nQC filter"],
                        fontsize=10, fontweight="bold")
    ax2.set_title(lbl, fontsize=14, fontweight="bold", pad=8)
    ax2.set_xlim(0.4, 2.6)

    removed = sum(r - f for r, f in zip(r_data, f_data))
    pct     = removed / sum(r_data) * 100
    ax2.text(0.98, 0.01,
             f"n={len(r_data):,}   removed={removed:,} ({pct:.1f}%)",
             transform=ax2.transAxes, ha="right", va="bottom",
             fontsize=8, color="#666666")

fig2.suptitle("Effect of QC/blank exclusion on sample counts",
              fontsize=14, fontweight="bold", y=0.97)

for ext in ("pdf", "svg", "png"):
    p = OUT_DIR / f"figure_sample_violin_supp.{ext}"
    fig2.savefig(p, dpi=300 if ext == "png" else None, bbox_inches="tight")
    print(f"Saved: {p}")
plt.close(fig2)
