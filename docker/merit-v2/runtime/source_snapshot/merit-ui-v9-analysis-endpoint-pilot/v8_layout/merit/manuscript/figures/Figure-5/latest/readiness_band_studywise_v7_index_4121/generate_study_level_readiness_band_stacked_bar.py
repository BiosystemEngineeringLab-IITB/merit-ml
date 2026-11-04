#!/usr/bin/env python3
"""Generate a study-level MERIT readiness-band stacked bar from local v7 cache."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

BAND_ORDER = ["Ready", "Conditional", "Fragile", "Not Ready", "No Data"]
BAND_COLORS = {
    "Ready": "#1b9e77",
    "Conditional": "#66a61e",
    "Fragile": "#e6ab02",
    "Not Ready": "#d95f02",
    "No Data": "#7570b3",
}
TEXT_DARK = "#17252a"
TEXT_MUTED = "#52666d"


def pct(count: int, total: int) -> float:
    return 100.0 * count / total if total else 0.0


def load_rows(index_path: Path) -> list[dict[str, object]]:
    with index_path.open() as fh:
        index = json.load(fh)
    studies = index.get("studies", {})
    rows = []
    for study_id, payload in sorted(studies.items()):
        band = payload.get("band") or "No Data"
        score = payload.get("score")
        rows.append(
            {
                "study_id": study_id,
                "readiness_band": band,
                "study_score": "" if score is None else score,
                "workflow_state_path": payload.get("state_path", ""),
                "updated_at_utc": payload.get("updated_at_utc", ""),
            }
        )
    return rows


def write_tsv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def make_plot(count_rows: list[dict[str, object]], out_dir: Path, stem: str) -> None:
    total = sum(int(r["count"]) for r in count_rows)

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.linewidth": 0.6,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    fig, ax = plt.subplots(figsize=(8.0, 1.42), constrained_layout=False)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    left = 0.0
    bar_y = 0.0
    bar_h = 0.34
    centers: dict[str, float] = {}
    widths: dict[str, float] = {}

    for row in count_rows:
        band = str(row["readiness_band"])
        width = float(row["percent"])
        count = int(row["count"])
        ax.barh(
            bar_y,
            width,
            left=left,
            height=bar_h,
            color=BAND_COLORS[band],
            edgecolor="white",
            linewidth=0.9,
        )
        centers[band] = left + width / 2
        widths[band] = width

        if width >= 8.0:
            txt_color = "white" if band in {"Ready", "Conditional", "Not Ready", "No Data"} else TEXT_DARK
            ax.text(
                left + width / 2,
                bar_y,
                f"{band}\n{count:,} ({width:.1f}%)",
                ha="center",
                va="center",
                color=txt_color,
                fontsize=8.5,
                fontweight="bold",
                linespacing=1.15,
            )
        left += width

    # Small-band callouts keep the bar narrow while preserving exact denominators.
    for band, y_text, dy in [("Fragile", 0.48, 0.21), ("No Data", -0.48, -0.21)]:
        row = next(r for r in count_rows if r["readiness_band"] == band)
        count = int(row["count"])
        width = float(row["percent"])
        ax.annotate(
            f"{band}: {count:,} ({width:.1f}%)",
            xy=(centers[band], bar_y + (bar_h / 2 if dy > 0 else -bar_h / 2)),
            xytext=(centers[band] + (5.2 if band == "Fragile" else -7.0), y_text),
            ha="left" if band == "Fragile" else "right",
            va="center",
            fontsize=7.8,
            color=TEXT_DARK,
            fontweight="bold",
            arrowprops={
                "arrowstyle": "-",
                "lw": 0.55,
                "color": TEXT_MUTED,
                "shrinkA": 0,
                "shrinkB": 0,
                "connectionstyle": "angle3,angleA=0,angleB=90",
            },
        )

    ax.set_xlim(0, 100)
    ax.set_ylim(-0.72, 0.72)
    ax.set_yticks([])
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.tick_params(axis="x", length=2.5, width=0.6, pad=2, colors=TEXT_MUTED)
    ax.set_xlabel(f"Fraction of studies (n={total:,})", fontsize=8.5, color=TEXT_MUTED, labelpad=4)
    ax.set_title(
        "Study-level MERIT readiness bands",
        loc="left",
        fontsize=10.5,
        fontweight="bold",
        color=TEXT_DARK,
        pad=5,
    )

    # Minimal legend is useful if this panel is cropped tightly in the manuscript layout.
    handles = [Patch(facecolor=BAND_COLORS[b], edgecolor="none", label=b) for b in BAND_ORDER]
    leg = ax.legend(
        handles=handles,
        ncol=5,
        frameon=False,
        loc="upper right",
        bbox_to_anchor=(1.0, 1.28),
        handlelength=0.9,
        columnspacing=0.9,
        handletextpad=0.35,
        fontsize=7.5,
    )
    for text in leg.get_texts():
        text.set_color(TEXT_MUTED)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#c7d0d3")
    ax.spines["bottom"].set_linewidth(0.6)

    fig.subplots_adjust(left=0.035, right=0.99, top=0.72, bottom=0.30)
    for ext in ["pdf", "svg"]:
        fig.savefig(out_dir / f"{stem}.{ext}", bbox_inches="tight")
    fig.savefig(out_dir / f"{stem}.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", default="merit-cache-workbench-full-v7")
    parser.add_argument(
        "--out-dir",
        default="merit/manuscript/figures/Figure-5/latest/readiness_band_stacked_bar_v7_study_level",
    )
    args = parser.parse_args()

    cache_root = Path(args.cache_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    index_path = cache_root / "index.json"
    rows = load_rows(index_path)
    total = len(rows)
    counts = Counter(str(r["readiness_band"]) for r in rows)

    count_rows = []
    for band in BAND_ORDER:
        count = counts.get(band, 0)
        count_rows.append(
            {
                "readiness_band": band,
                "count": count,
                "percent": f"{pct(count, total):.6f}",
            }
        )

    write_tsv(
        out_dir / "readiness_band_study_level_rows.tsv",
        rows,
        ["study_id", "readiness_band", "study_score", "workflow_state_path", "updated_at_utc"],
    )
    write_tsv(
        out_dir / "readiness_band_study_level_counts.tsv",
        count_rows,
        ["readiness_band", "count", "percent"],
    )

    with (out_dir / "README.md").open("w") as fh:
        fh.write("# Study-Level Readiness Band Stacked Bar\n\n")
        fh.write(f"Source cache: `{cache_root}`\n\n")
        fh.write(f"Analytic unit: one study from `{index_path}` (`n={total:,}`).\n\n")
        fh.write("Band counts:\n\n")
        for row in count_rows:
            fh.write(
                f"- {row['readiness_band']}: {int(row['count']):,} "
                f"({float(row['percent']):.1f}%)\n"
            )

    make_plot(count_rows, out_dir, "figure5_readiness_band_stacked_bar_study_level")

    print(f"Wrote study-level readiness plot set to {out_dir}")
    for row in count_rows:
        print(f"{row['readiness_band']}\t{row['count']}\t{float(row['percent']):.3f}%")


if __name__ == "__main__":
    main()
