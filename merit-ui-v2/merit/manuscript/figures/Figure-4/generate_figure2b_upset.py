#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

matplotlib.rc("font", family="DejaVu Sans")
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"]  = 42
matplotlib.rcParams["font.size"]    = 16

SRC_TSV = Path("/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv")
OUT_DIR = Path("/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-2")

# Bit order for intersection keys: mwtab, datatable, untarg_data.
KEY_ORDER = ["110", "001", "111", "100", "101", "010", "011", "000"]


def compute_partitions(tsv: Path) -> tuple[dict[str, int], dict[str, int], int]:
    src = pd.read_csv(tsv, sep="\t")
    combos: dict[str, int] = {k: 0 for k in ["000","001","010","011","100","101","110","111"]}
    for _, row in src.iterrows():
        key = (
            ("1" if row["mwtab_valid_present"]     else "0") +
            ("1" if row["datatable_valid_present"]  else "0") +
            ("1" if row["untarg_valid_present"]     else "0")
        )
        combos[key] += 1
    set_sizes = {
        "mwtab":       combos["100"] + combos["101"] + combos["110"] + combos["111"],
        "datatable":   combos["010"] + combos["011"] + combos["110"] + combos["111"],
        "untarg_data": combos["001"] + combos["011"] + combos["101"] + combos["111"],
    }
    return combos, set_sizes, len(src)


def plot_upset(combos: dict[str, int], set_sizes: dict[str, int], total: int, out_dir: Path) -> None:
    labels = {
        "110": "mwtab ∩\ndatatable",
        "001": "untarg\nonly",
        "111": "all 3",
        "100": "mwtab\nonly",
        "101": "mwtab ∩\nuntarg",
        "010": "datatable\nonly",
        "011": "datatable ∩\nuntarg",
        "000": "none",
    }
    counts = [combos[k] for k in KEY_ORDER]
    x = list(range(len(KEY_ORDER)))

    # ── Colors matching figure2a palette ────────────────────────────────────
    COLOR_ALL3   = "#2C7FB8"   # blue   — all three sources
    COLOR_MW_DT  = "#1B9E77"   # green  — mwtab+datatable (most common pair)
    COLOR_SINGLE = "#2a9d6a"   # teal   — any single-source or other pair
    COLOR_NONE   = "#c0392b"   # red    — no valid source
    SETBAR_COLOR = "#2B6CB0"   # blue   — set-size bars

    bar_colors = []
    for key in KEY_ORDER:
        if key == "111":
            bar_colors.append(COLOR_ALL3)
        elif key == "110":
            bar_colors.append(COLOR_MW_DT)
        elif key == "000":
            bar_colors.append(COLOR_NONE)
        else:
            bar_colors.append(COLOR_SINGLE)

    fig = plt.figure(figsize=(15, 7.5))
    grid = fig.add_gridspec(
        2, 2,
        width_ratios=[1.3, 5.7],
        height_ratios=[3.6, 1.6],
        wspace=0.25, hspace=0.08,
    )
    ax_sets  = fig.add_subplot(grid[:, 0])
    ax_bars  = fig.add_subplot(grid[0, 1])
    ax_mat   = fig.add_subplot(grid[1, 1], sharex=ax_bars)

    # ── Left panel: set sizes ────────────────────────────────────────────────
    set_names  = ["mwtab", "datatable", "untarg_data"]
    set_values = [set_sizes[n] for n in set_names]
    ax_sets.barh(set_names, set_values, color=SETBAR_COLOR, alpha=0.88)
    for y_pos, val in enumerate(set_values):
        ax_sets.text(
            val + max(set_values) * 0.015, y_pos,
            f"{val:,}",
            va="center", ha="left",
            fontsize=16, fontweight="bold", color="#132327",
        )
    ax_sets.set_title("Set size", fontsize=17, fontweight="bold", pad=8)
    ax_sets.set_xlabel("Analyses count", fontsize=16, fontweight="bold")
    ax_sets.invert_yaxis()
    ax_sets.spines[["top", "right"]].set_visible(False)
    ax_sets.tick_params(axis="both", labelsize=15)
    for tick in ax_sets.get_xticklabels() + ax_sets.get_yticklabels():
        tick.set_fontweight("bold")
    ax_sets.xaxis.grid(True, linestyle="--", alpha=0.4, zorder=0)
    ax_sets.set_axisbelow(True)

    # ── Top-right panel: intersection bars ──────────────────────────────────
    bars = ax_bars.bar(x, counts, color=bar_colors, width=0.78, zorder=3)
    for rect, val in zip(bars, counts):
        ax_bars.text(
            rect.get_x() + rect.get_width() / 2.0,
            rect.get_height() + max(counts) * 0.012,
            f"{val:,}",
            ha="center", va="bottom",
            fontsize=15, fontweight="bold", color="#132327",
        )
    ax_bars.set_ylabel("Intersection size\n(analyses)", fontsize=16, fontweight="bold")
    ax_bars.set_xticks([])
    ax_bars.spines[["top", "right"]].set_visible(False)
    ax_bars.tick_params(axis="y", labelsize=15)
    for tick in ax_bars.get_yticklabels():
        tick.set_fontweight("bold")
    ax_bars.yaxis.grid(True, linestyle="--", alpha=0.4, zorder=0)
    ax_bars.set_axisbelow(True)

    # ── Bottom-right panel: dot matrix ──────────────────────────────────────
    rows      = ["mwtab", "datatable", "untarg_data"]
    row_to_y  = {name: idx for idx, name in enumerate(rows)}
    for xi, key in zip(x, KEY_ORDER):
        active_rows: list[int] = []
        for bit, row_name in zip(key, rows):
            y_pos = row_to_y[row_name]
            if bit == "1":
                ax_mat.plot(xi, y_pos, "o", color="#1A202C", markersize=9, zorder=3)
                active_rows.append(y_pos)
            else:
                ax_mat.plot(xi, y_pos, "o", color="#CBD5E0", markersize=7, zorder=3)
        if len(active_rows) >= 2:
            ax_mat.plot(
                [xi, xi], [min(active_rows), max(active_rows)],
                color="#1A202C", linewidth=2.0, zorder=2,
            )

    ax_mat.set_yticks([0, 1, 2])
    ax_mat.set_yticklabels(rows, fontsize=15, fontweight="bold")
    ax_mat.set_ylim(-0.5, 2.5)
    ax_mat.invert_yaxis()
    ax_mat.set_xticks(x)
    ax_mat.set_xticklabels([""] * len(KEY_ORDER))
    ax_mat.set_xlabel("")
    ax_mat.spines[["top", "right"]].set_visible(False)
    ax_mat.tick_params(axis="both", labelsize=15)

    # ── Legend ───────────────────────────────────────────────────────────────
    legend_handles = [
        mpatches.Patch(color=COLOR_ALL3,   label="All 3 sources valid"),
        mpatches.Patch(color=COLOR_MW_DT,  label="mwtab ∩ datatable"),
        mpatches.Patch(color=COLOR_SINGLE, label="Single / other pair"),
        mpatches.Patch(color=COLOR_NONE,   label="No valid source"),
    ]
    ax_bars.legend(
        handles=legend_handles,
        fontsize=14, prop={"weight": "bold"},
        framealpha=0.92,
        bbox_to_anchor=(1.01, 1), loc="upper left",
        borderaxespad=0,
    )

    # ── Title and footnote ───────────────────────────────────────────────────
    fig.suptitle(
        f"Figure 2B. UpSet overlap of valid tabular data sources across {total:,} analyses",
        fontsize=17, fontweight="bold", y=0.99,
    )
    fig.text(
        0.5, 0.005,
        "Validity: >2-column header + at least one non-header numeric row; "
        "mwtab requires numeric rows inside recognized *_METABOLITE_DATA blocks.",
        ha="center", fontsize=11, color="#c0392b", fontstyle="italic",
    )

    # ── Save ─────────────────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "figure2b_upset.pdf", bbox_inches="tight")
    fig.savefig(out_dir / "figure2b_upset.svg", bbox_inches="tight")
    fig.savefig(out_dir / "figure2b_upset.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("Saved to", out_dir)


def main() -> None:
    combos, set_sizes, total = compute_partitions(SRC_TSV)
    print("total:", total)
    print("set_sizes:", set_sizes)
    print("combos:", combos)
    plot_upset(combos, set_sizes, total, OUT_DIR)


if __name__ == "__main__":
    main()
