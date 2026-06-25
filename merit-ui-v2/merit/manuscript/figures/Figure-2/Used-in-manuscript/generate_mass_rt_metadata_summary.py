#!/usr/bin/env python3
"""Summarize study-level mass/mz-like and RT/RI-like metadata evidence.

The plot is derived from the v7 MERIT workflow-state cache. Each study can
contain repeated metric objects because the report is rendered for multiple
sources; this script collapses those repeated objects to one study-level row.
"""

from __future__ import annotations

import csv
import json
import textwrap
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path("/home/shayantan/metabolomics/ML-ready")
CACHE_JSON = ROOT / "merit-cache-workbench-full-v7" / "json"
OUT = ROOT / "merit" / "manuscript" / "figures" / "Figure-2" / "Used-in-manuscript"
TOTAL_STUDIES = 4121

MASS_MZ_CLASSES = {"mass-like", "mz-like", "ambiguous"}
RT_CLASSES = {"rt-like"}

AVAIL_ORDER = [
    "Both mass/mz-like and RT/RI-like",
    "Mass/mz-like only",
    "RT/RI-like only",
    "None",
]
AVAIL_COLORS = {
    "Both mass/mz-like and RT/RI-like": "#0d6e6e",
    "Mass/mz-like only": "#2f80ed",
    "RT/RI-like only": "#f2994a",
    "None": "#d7dee0",
}

UNIT_ORDER = [
    "Minutes only",
    "Seconds only",
    "Mixed/other RT-unit value",
    "No RT unit reported",
]
UNIT_COLORS = {
    "Minutes only": "#4c78a8",
    "Seconds only": "#f58518",
    "Mixed/other RT-unit value": "#7a5195",
    "No RT unit reported": "#d7dee0",
}
DISPLAY_LABELS = {
    "Both mass/mz-like and RT/RI-like": "Both",
    "Mass/mz-like only": "Mass/mz\nonly",
    "RT/RI-like only": "RT/RI\nonly",
    "None": "None",
    "Minutes only": "Minutes",
    "Seconds only": "Seconds",
    "Mixed/other RT-unit value": "Mixed/\nother",
    "No RT unit reported": "No RT unit\nreported",
}


def iter_metric_objects(obj: Any) -> list[dict[str, Any]]:
    """Return every serialized mass_rt_like_metadata_presence metric object."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if obj.get("name") == "mass_rt_like_metadata_presence":
            found.append(obj)
        for value in obj.values():
            found.extend(iter_metric_objects(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(iter_metric_objects(value))
    return found


def normalize_rt_unit(value: str) -> str:
    text = str(value or "").strip()
    x = text.lower()
    if not x:
        return ""
    if x in {"min", "mins", "minute", "minutes"} or "minute" in x:
        return "Minutes"
    if x in {"sec", "secs", "second", "seconds"} or "second" in x:
        return "Seconds"
    if x == "no rt data":
        return "No RT data"
    return "Other/non-time"


def unit_category(normalized_units: set[str]) -> str:
    if not normalized_units:
        return "No RT unit reported"
    if normalized_units == {"Minutes"}:
        return "Minutes only"
    if normalized_units == {"Seconds"}:
        return "Seconds only"
    if normalized_units == {"No RT data"}:
        return "No RT unit reported"
    return "Mixed/other RT-unit value"


def availability_category(classes: set[str]) -> str:
    mass_mz = bool(classes & MASS_MZ_CLASSES)
    rt_ri = bool(classes & RT_CLASSES)
    if mass_mz and rt_ri:
        return "Both mass/mz-like and RT/RI-like"
    if mass_mz:
        return "Mass/mz-like only"
    if rt_ri:
        return "RT/RI-like only"
    return "None"


def collect_rows() -> tuple[list[dict[str, str]], Counter, Counter, Counter]:
    rows: list[dict[str, str]] = []
    raw_unit_to_studies: dict[str, set[str]] = defaultdict(set)
    raw_class_counter: Counter = Counter()

    workflow_paths = sorted(CACHE_JSON.glob("st*_workflow_state.json"))
    if len(workflow_paths) != TOTAL_STUDIES:
        raise RuntimeError(f"Expected {TOTAL_STUDIES} workflow states, found {len(workflow_paths)}")

    for path in workflow_paths:
        study_id = path.name.split("_", 1)[0].upper()
        payload = json.loads(path.read_text(encoding="utf-8"))
        metric_objects = iter_metric_objects(payload)

        classes: set[str] = set()
        fields_by_class: dict[str, set[str]] = defaultdict(set)
        raw_units: set[str] = set()
        normalized_units: set[str] = set()

        for metric in metric_objects:
            details = metric.get("details") or {}
            field_classes = details.get("field_classes") or {}
            if isinstance(field_classes, dict):
                for field_name, field_class in field_classes.items():
                    c = str(field_class or "").strip()
                    f = str(field_name or "").strip()
                    if c:
                        classes.add(c)
                        raw_class_counter[c] += 1
                    if c and f:
                        fields_by_class[c].add(f)

            rt_meta = details.get("rt_units_ms_results_file_metadata") or {}
            values = rt_meta.get("rt_units_values") or []
            if isinstance(values, list):
                for value in values:
                    raw = str(value or "").strip()
                    norm = normalize_rt_unit(raw)
                    if raw:
                        raw_units.add(raw)
                        raw_unit_to_studies[raw].add(study_id)
                    if norm:
                        normalized_units.add(norm)

        rows.append(
            {
                "study_id": study_id,
                "metric_occurrences": str(len(metric_objects)),
                "field_classes": ";".join(sorted(classes)),
                "mass_mz_like_present": str(bool(classes & MASS_MZ_CLASSES)).lower(),
                "rt_ri_like_present": str(bool(classes & RT_CLASSES)).lower(),
                "availability_category": availability_category(classes),
                "fields_mass_like": ";".join(sorted(fields_by_class.get("mass-like", set()))),
                "fields_mz_like": ";".join(sorted(fields_by_class.get("mz-like", set()))),
                "fields_rt_like": ";".join(sorted(fields_by_class.get("rt-like", set()))),
                "fields_ambiguous": ";".join(sorted(fields_by_class.get("ambiguous", set()))),
                "rt_unit_values_raw": ";".join(sorted(raw_units)),
                "rt_unit_values_normalized": ";".join(sorted(normalized_units)),
                "rt_unit_category": unit_category(normalized_units),
            }
        )

    availability_counts = Counter(row["availability_category"] for row in rows)
    unit_counts = Counter(row["rt_unit_category"] for row in rows)
    raw_unit_counts = Counter({unit: len(studies) for unit, studies in raw_unit_to_studies.items()})
    return rows, availability_counts, unit_counts, raw_unit_counts


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def draw_stacked_bar(
    ax: plt.Axes,
    counts: Counter,
    order: list[str],
    colors: dict[str, str],
    title: str,
    total: int,
) -> None:
    left = 0
    small_annotation_index = 0
    for label in order:
        count = int(counts.get(label, 0))
        if count == 0:
            continue
        color = colors[label]
        ax.barh(0, count, left=left, height=0.55, color=color, edgecolor="white", linewidth=1.0)
        pct = 100.0 * count / total
        x = left + count / 2
        if pct >= 12.0:
            text_color = "white" if label != "None" and label != "No RT unit reported" else "#132327"
            display = DISPLAY_LABELS.get(label, label).replace("\n", " ")
            text = f"{display}\n{count:,} ({pct:.1f}%)"
            ax.text(
                x,
                0,
                text,
                ha="center",
                va="center",
                fontsize=8.2,
                fontweight="bold",
                color=text_color,
            )
        elif pct >= 0.5:
            display = DISPLAY_LABELS.get(label, label).replace("\n", " ")
            y_text = 0.73 + 0.20 * (small_annotation_index % 2)
            small_annotation_index += 1
            ax.annotate(
                f"{display}: {count:,} ({pct:.1f}%)",
                xy=(x, 0.31),
                xytext=(x, y_text),
                ha="center",
                va="bottom",
                fontsize=7.4,
                fontweight="bold",
                color="#132327",
                arrowprops=dict(arrowstyle="-", color="#8a9a9d", lw=0.7, shrinkA=1, shrinkB=1),
            )
        left += count

    ax.set_xlim(0, total)
    ax.set_ylim(-0.55, 1.15)
    ax.set_yticks([])
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", pad=4)
    ax.set_xlabel("Fraction of studies (same denominator: n=4,121)", fontsize=10, fontweight="bold")
    tick_fracs = [0, 0.25, 0.50, 0.75, 1.00]
    ax.set_xticks([total * f for f in tick_fracs])
    ax.set_xticklabels([f"{int(f * 100)}%" for f in tick_fracs], fontsize=9)
    ax.grid(axis="x", linestyle="--", alpha=0.25)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)


def make_plot(availability_counts: Counter, unit_counts: Counter, raw_unit_counts: Counter) -> None:
    plt.rcParams["font.family"] = "DejaVu Sans"
    plt.rcParams["pdf.fonttype"] = 42
    plt.rcParams["ps.fonttype"] = 42

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(9.2, 4.8),
        gridspec_kw={"height_ratios": [1, 1.05], "hspace": 0.58},
    )

    draw_stacked_bar(
        axes[0],
        availability_counts,
        AVAIL_ORDER,
        AVAIL_COLORS,
        "A. Populated mass/mz-like and RT/RI-like metabolite metadata",
        TOTAL_STUDIES,
    )
    draw_stacked_bar(
        axes[1],
        unit_counts,
        UNIT_ORDER,
        UNIT_COLORS,
        "B. RT-unit declarations in mwTab MS_RESULTS_FILE metadata",
        TOTAL_STUDIES,
    )

    any_units = TOTAL_STUDIES - unit_counts.get("No RT unit reported", 0)
    raw_unit_text = "\n".join(
        f"{unit}: {count:,}"
        for unit, count in sorted(raw_unit_counts.items(), key=lambda item: (-item[1], item[0].lower()))
    )
    axes[1].text(
        0.995,
        0.95,
        f"Reported unit-like value: {any_units:,}/{TOTAL_STUDIES:,} ({100 * any_units / TOTAL_STUDIES:.1f}%)\n"
        f"Raw RT-unit field strings by study:\n{raw_unit_text}",
        transform=axes[1].transAxes,
        ha="right",
        va="top",
        fontsize=8.4,
        color="#132327",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor="#d7dee0", alpha=0.95),
    )

    fig.suptitle(
        "Mass/RT-like metabolite metadata in Metabolomics Workbench studies",
        fontsize=13.5,
        fontweight="bold",
        y=0.98,
    )
    note = (
        "Study-level aggregation from v7 MERIT workflow states. "
        "Mass/mz-like includes mass-like, m/z/moverz-like, and retained ambiguous coordinate-like labels; "
        "RT/RI-like includes retention time and retention index labels."
    )
    fig.text(
        0.07,
        0.035,
        textwrap.fill(note, width=150),
        fontsize=8.0,
        color="#51656a",
    )
    fig.subplots_adjust(top=0.86, bottom=0.20, left=0.07, right=0.98)

    for ext in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"figure2_mass_rt_metadata_summary.{ext}", dpi=300)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, availability_counts, unit_counts, raw_unit_counts = collect_rows()

    write_tsv(
        OUT / "mass_rt_metadata_study_level.tsv",
        rows,
        [
            "study_id",
            "metric_occurrences",
            "field_classes",
            "mass_mz_like_present",
            "rt_ri_like_present",
            "availability_category",
            "fields_mass_like",
            "fields_mz_like",
            "fields_rt_like",
            "fields_ambiguous",
            "rt_unit_values_raw",
            "rt_unit_values_normalized",
            "rt_unit_category",
        ],
    )

    availability_rows = [
        {
            "category": label,
            "count": availability_counts.get(label, 0),
            "percent": round(100.0 * availability_counts.get(label, 0) / TOTAL_STUDIES, 3),
        }
        for label in AVAIL_ORDER
    ]
    write_tsv(
        OUT / "mass_rt_availability_summary.tsv",
        availability_rows,
        ["category", "count", "percent"],
    )

    unit_rows = [
        {
            "category": label,
            "count": unit_counts.get(label, 0),
            "percent": round(100.0 * unit_counts.get(label, 0) / TOTAL_STUDIES, 3),
        }
        for label in UNIT_ORDER
    ]
    write_tsv(OUT / "rt_unit_category_summary.tsv", unit_rows, ["category", "count", "percent"])

    raw_unit_rows = [
        {
            "raw_rt_unit_value": unit,
            "study_count": count,
            "percent_of_all_studies": round(100.0 * count / TOTAL_STUDIES, 3),
        }
        for unit, count in sorted(raw_unit_counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]
    write_tsv(
        OUT / "rt_unit_raw_value_summary.tsv",
        raw_unit_rows,
        ["raw_rt_unit_value", "study_count", "percent_of_all_studies"],
    )

    make_plot(availability_counts, unit_counts, raw_unit_counts)

    print("Wrote:")
    for name in [
        "figure2_mass_rt_metadata_summary.pdf",
        "figure2_mass_rt_metadata_summary.png",
        "figure2_mass_rt_metadata_summary.svg",
        "mass_rt_metadata_study_level.tsv",
        "mass_rt_availability_summary.tsv",
        "rt_unit_category_summary.tsv",
        "rt_unit_raw_value_summary.tsv",
    ]:
        print(f"  {OUT / name}")
    print("\nAvailability counts:", dict(availability_counts))
    print("RT-unit category counts:", dict(unit_counts))
    print("Raw RT-unit values:", dict(raw_unit_counts))


if __name__ == "__main__":
    main()
