#!/usr/bin/env python3
"""Generate a narrow stacked barplot of v7 readiness bands at analysis level.

Each Workbench analysis directory (ST*/AN*) contributes one row. Source selection
is source-aware and non-duplicating: datatable > mwtab > untarg_data > No Data.
The selected source's source-level readiness band is assigned to all analyses
covered by that source in that study. Analyses with no valid source are No Data.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path("/home/shayantan/metabolomics/ML-ready")
DUMP_ROOT = ROOT / "mw-dump-latest-confirmation-latest-version"
CACHE_JSON = ROOT / "merit-cache-workbench-full-v7" / "json"
OUT_DIR = ROOT / "merit" / "manuscript" / "figures" / "Figure-5" / "latest" / "readiness_band_stacked_bar_v7_analysis_level"

BAND_ORDER = ["Ready", "Conditional", "Fragile", "Not Ready", "No Data"]
BAND_COLORS = {
    "Ready": "#1b9e77",
    "Conditional": "#66a61e",
    "Fragile": "#e6ab02",
    "Not Ready": "#d95f02",
    "No Data": "#7570b3",
}
SOURCE_ORDER = ["datatable", "mwtab", "untarg_data"]


def norm_band(value: object) -> str:
    text = str(value or "").strip()
    lookup = {b.lower(): b for b in BAND_ORDER}
    return lookup.get(text.lower(), text if text else "No Data")


def registered_analyses() -> list[tuple[str, str]]:
    rows = []
    for an_dir in sorted(DUMP_ROOT.glob("ST*/AN*")):
        if an_dir.is_dir():
            rows.append((an_dir.parent.name.upper(), an_dir.name.upper()))
    return rows


def selected_rows() -> list[dict[str, object]]:
    rows = []
    for study_id, analysis_id in registered_analyses():
        workflow_path = CACHE_JSON / f"{study_id.lower()}_workflow_state.json"
        selected_source = "none"
        band = "No Data"
        score = 0.0
        source_tier = ""
        reason = "no_valid_source"
        if workflow_path.exists():
            payload = json.loads(workflow_path.read_text(encoding="utf-8"))
            source_availability = payload.get("source_availability") or {}
            analyses_by_source = source_availability.get("analyses_by_source") or {}
            source_assessments = payload.get("source_assessments") or {}
            for source in SOURCE_ORDER:
                available = {str(x).upper() for x in analyses_by_source.get(source, [])}
                if analysis_id not in available:
                    continue
                assessment = source_assessments.get(source) if isinstance(source_assessments, dict) else None
                readiness = (assessment or {}).get("readiness_score") if isinstance(assessment, dict) else None
                if not isinstance(readiness, dict):
                    continue
                selected_source = source
                band = norm_band(readiness.get("final_band") or readiness.get("band"))
                score = readiness.get("core_ml_readiness_score", readiness.get("score", ""))
                source_tier = readiness.get("source_tier", "")
                reason = "selected_priority_source"
                break
            if selected_source == "none":
                top = payload.get("readiness_score") or {}
                if norm_band(top.get("final_band") or top.get("band")) == "No Data":
                    source_tier = top.get("source_tier", "")
                    score = top.get("core_ml_readiness_score", top.get("score", 0.0))
                reason = "no_valid_source_in_v7_cache"
        else:
            reason = "missing_workflow_state"
        rows.append(
            {
                "study_id": study_id,
                "analysis_id": analysis_id,
                "selected_source": selected_source,
                "band": band,
                "score": score,
                "source_tier": source_tier,
                "reason": reason,
            }
        )
    return rows


def write_tsv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def make_plot(counts: Counter, total: int) -> None:
    plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42

    fig, ax = plt.subplots(figsize=(7.4, 1.05))
    left = 0
    small_label_index = 0
    for band in BAND_ORDER:
        count = counts.get(band, 0)
        if count == 0:
            continue
        pct = count / total * 100
        ax.barh(0, count, left=left, height=0.28, color=BAND_COLORS[band], edgecolor="white", linewidth=0.8)
        x = left + count / 2
        if pct >= 7:
            color = "white" if band in {"Ready", "Not Ready", "No Data"} else "#182528"
            ax.text(
                x,
                0,
                f"{band} {count:,} ({pct:.1f}%)",
                ha="center",
                va="center",
                fontsize=6.8,
                fontweight="bold",
                color=color,
            )
        else:
            y_text = 0.27 + 0.14 * (small_label_index % 2)
            small_label_index += 1
            ax.annotate(
                f"{band}: {count:,} ({pct:.1f}%)",
                xy=(x, 0.15),
                xytext=(x, y_text),
                ha="center",
                va="bottom",
                fontsize=6.4,
                fontweight="bold",
                color="#182528",
                arrowprops=dict(arrowstyle="-", color="#87979a", lw=0.7),
            )
        left += count

    ax.set_xlim(0, total)
    ax.set_ylim(-0.30, 0.54)
    ax.set_yticks([])
    ax.set_xticks([0, total * 0.25, total * 0.5, total * 0.75, total])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=7.4)
    ax.set_xlabel(f"Fraction of analyses (n={total:,})", fontsize=8.0, fontweight="bold", labelpad=1)
    ax.set_title("Repository-wide MERIT readiness bands", loc="left", fontsize=9.0, fontweight="bold", pad=4)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_linewidth(0.8)
    fig.subplots_adjust(left=0.035, right=0.995, top=0.68, bottom=0.36)

    for ext in ["pdf", "svg"]:
        fig.savefig(OUT_DIR / f"figure5_readiness_band_stacked_bar.{ext}", bbox_inches="tight")
    fig.savefig(OUT_DIR / "figure5_readiness_band_stacked_bar.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = selected_rows()
    total = len(rows)
    counts = Counter(row["band"] for row in rows)
    source_counts = Counter(row["selected_source"] for row in rows)
    reason_counts = Counter(row["reason"] for row in rows)

    write_tsv(
        OUT_DIR / "readiness_band_analysis_level_rows.tsv",
        rows,
        ["study_id", "analysis_id", "selected_source", "band", "score", "source_tier", "reason"],
    )
    summary_rows = [
        {
            "band": band,
            "count": counts.get(band, 0),
            "percent": round(counts.get(band, 0) / total * 100, 3),
        }
        for band in BAND_ORDER
    ]
    write_tsv(OUT_DIR / "readiness_band_counts.tsv", summary_rows, ["band", "count", "percent"])
    write_tsv(
        OUT_DIR / "readiness_band_source_selection_counts.tsv",
        [
            {"selected_source": k, "count": v, "percent": round(v / total * 100, 3)}
            for k, v in sorted(source_counts.items())
        ],
        ["selected_source", "count", "percent"],
    )
    write_tsv(
        OUT_DIR / "readiness_band_assignment_reason_counts.tsv",
        [
            {"reason": k, "count": v, "percent": round(v / total * 100, 3)}
            for k, v in sorted(reason_counts.items())
        ],
        ["reason", "count", "percent"],
    )
    make_plot(counts, total)
    print(f"Wrote outputs to {OUT_DIR}")
    print("Total analyses", total)
    print("Band counts", {band: counts.get(band, 0) for band in BAND_ORDER})
    print("Source counts", dict(source_counts))
    print("Reason counts", dict(reason_counts))


if __name__ == "__main__":
    main()
