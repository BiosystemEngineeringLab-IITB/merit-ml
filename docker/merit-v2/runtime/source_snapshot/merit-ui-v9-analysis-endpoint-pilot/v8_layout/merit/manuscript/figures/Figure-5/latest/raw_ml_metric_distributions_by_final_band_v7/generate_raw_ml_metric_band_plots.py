#!/usr/bin/env python3
"""Plot raw ML-relevant quantities by final MERIT readiness band, studywise."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

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
GRID = "#d8e0e2"
THRESHOLD = "#2f3f45"


def as_num(x: Any) -> float | None:
    try:
        if x in (None, ""):
            return None
        y = float(x)
        if math.isnan(y) or math.isinf(y):
            return None
        return y
    except Exception:
        return None


def find_metric(report: dict[str, Any], metric_name: str) -> dict[str, Any] | None:
    for section in [
        "schema_validation",
        "analytical_readiness",
        "annotation_readiness",
        "cohort_bias",
        "ml_readiness",
        "metadata_fair",
    ]:
        for metric in report.get(section, []) or []:
            if isinstance(metric, dict) and metric.get("name") == metric_name:
                return metric
    return None


def pct(x: float | None) -> float | None:
    return None if x is None else 100.0 * x


def metric_details(report: dict[str, Any], name: str) -> dict[str, Any]:
    metric = find_metric(report, name) or {}
    return metric.get("details") or {}


def load_rows(cache_root: Path) -> list[dict[str, Any]]:
    index = json.load((cache_root / "index.json").open())
    rows: list[dict[str, Any]] = []

    for study_id, payload in sorted(index.get("studies", {}).items()):
        state = json.load((cache_root / payload["state_path"]).open())
        report = state.get("final_report") or {}
        rs = state.get("readiness_score") or {}
        row: dict[str, Any] = {
            "study_id": study_id,
            "final_band": payload.get("band") or "No Data",
            "study_score": payload.get("score", ""),
            "primary_source": state.get("primary_source") or state.get("source") or "",
            "workflow_state_path": payload.get("state_path", ""),
            "gate_ceiling": rs.get("gate_ceiling") or "",
            "provisional_band": rs.get("provisional_band") or "",
        }

        for gate in rs.get("gates", []) or []:
            gid = str(gate.get("id"))
            row[f"{gid}_status"] = gate.get("status", "")
            row[f"{gid}_value"] = as_num(gate.get("value"))

        d = metric_details(report, "minimum_sample_count")
        row["n_biological_samples"] = as_num(d.get("n_biological_samples"))
        row["n_total_samples"] = as_num(d.get("n_total_samples"))
        row["minimum_sample_threshold"] = as_num(d.get("threshold"))

        d = metric_details(report, "tabular_data_availability")
        row["n_feature_matrices"] = as_num(d.get("n_with_data"))
        row["n_matrices_total"] = as_num(d.get("n_matrices"))

        d = metric_details(report, "group_size_support")
        counts = d.get("counts") or {}
        class_values: list[int] = []
        if isinstance(counts, dict):
            class_values = [int(v) for v in (as_num(v) for v in counts.values()) if v is not None]
        row["n_classes"] = len(class_values) if class_values else as_num(d.get("n_classes"))
        row["min_class_count"] = min(class_values) if class_values else as_num(d.get("min_group_size"))
        row["max_class_count"] = max(class_values) if class_values else None
        row["class_balance_min_over_max"] = (
            min(class_values) / max(class_values) if class_values and max(class_values) > 0 else None
        )

        d = metric_details(report, "label_entropy")
        row["label_entropy_norm"] = as_num(d.get("entropy_norm"))

        d = metric_details(report, "disease_endpoint_extractability")
        row["distinct_label_groups"] = as_num(d.get("distinct_label_groups"))
        row["label_coverage_pct"] = pct(as_num(d.get("label_coverage")))

        d = metric_details(report, "feature_to_sample_ratio")
        row["n_features_total_all_matrices"] = as_num(
            d.get("n_features_total_all_matrices") or d.get("total_features")
        )
        row["median_pn_ratio"] = as_num(d.get("median_pn_ratio") or d.get("ratio"))
        row["worst_pn_ratio"] = as_num(d.get("worst_ratio"))
        row["pct_analyses_pn_gt1"] = as_num(d.get("pct_analyses_pn_gt1"))

        d = metric_details(report, "missingness_structure")
        row["median_sample_missingness_pct"] = pct(as_num(d.get("global_median_sample_missingness_rate")))
        row["mean_sample_missingness_pct"] = pct(as_num(d.get("global_mean_sample_missingness_rate")))
        row["class_dependent_missingness_gap_pct"] = pct(as_num(d.get("class_dependent_gap_weighted")))

        d = metric_details(report, "feature_level_missingness")
        row["mean_feature_missingness_pct"] = pct(as_num(d.get("mean_missingness_rate")))
        row["median_feature_missingness_pct"] = pct(as_num(d.get("median_missingness_rate")))
        row["pct_features_over_30pct_missingness"] = pct(as_num(d.get("pct_features_over_threshold")))
        row["n_high_missing_features"] = as_num(d.get("n_high_missing_features"))

        d = metric_details(report, "outlier_burden")
        outliers = as_num(d.get("sample_outliers"))
        total = as_num(d.get("sample_total"))
        row["sample_outlier_rate_pct"] = 100.0 * outliers / total if total and outliers is not None else None
        row["sample_outliers"] = outliers
        row["sample_outlier_denominator"] = total

        d = metric_details(report, "feature_correlation_burden")
        high = as_num(d.get("high_correlation_pairs"))
        sampled = as_num(d.get("sampled_pairs"))
        row["high_corr_pair_rate_pct"] = 100.0 * high / sampled if sampled and high is not None else None
        row["high_correlation_pairs"] = high
        row["sampled_correlation_pairs"] = sampled

        rows.append(row)
    return rows


def clean_values(rows: list[dict[str, Any]], band: str, key: str) -> list[float]:
    vals = []
    for row in rows:
        if row.get("final_band") != band:
            continue
        value = as_num(row.get(key))
        if value is not None:
            vals.append(value)
    return vals


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.array(values, dtype=float), q))


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if not rows:
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict[str, Any]], specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for spec in specs:
        key = spec["key"]
        for band in BAND_ORDER:
            vals = clean_values(rows, band, key)
            if not vals:
                out.append(
                    {
                        "metric_key": key,
                        "metric_label": spec["title"],
                        "final_band": band,
                        "n": 0,
                        "median": "",
                        "p25": "",
                        "p75": "",
                        "p10": "",
                        "p90": "",
                    }
                )
                continue
            out.append(
                {
                    "metric_key": key,
                    "metric_label": spec["title"],
                    "final_band": band,
                    "n": len(vals),
                    "median": f"{quantile(vals, 0.5):.8g}",
                    "p25": f"{quantile(vals, 0.25):.8g}",
                    "p75": f"{quantile(vals, 0.75):.8g}",
                    "p10": f"{quantile(vals, 0.10):.8g}",
                    "p90": f"{quantile(vals, 0.90):.8g}",
                }
            )
    return out


def plot_panel(ax: plt.Axes, rows: list[dict[str, Any]], spec: dict[str, Any], rng: np.random.Generator) -> None:
    key = spec["key"]
    positions = np.arange(len(BAND_ORDER), 0, -1)
    data = [clean_values(rows, band, key) for band in BAND_ORDER]
    nonempty = [(pos, vals, band) for pos, vals, band in zip(positions, data, BAND_ORDER) if vals]

    if not nonempty:
        ax.text(0.5, 0.5, "No values", ha="center", va="center", transform=ax.transAxes, color=TEXT_MUTED)
        return

    bp = ax.boxplot(
        [vals for _, vals, _ in nonempty],
        positions=[pos for pos, _, _ in nonempty],
        vert=False,
        widths=0.58,
        patch_artist=True,
        showfliers=False,
        whis=(5, 95),
        medianprops={"color": TEXT_DARK, "linewidth": 1.8},
        boxprops={"linewidth": 0.9, "color": TEXT_DARK},
        whiskerprops={"linewidth": 0.85, "color": TEXT_DARK},
        capprops={"linewidth": 0.85, "color": TEXT_DARK},
    )
    for patch, (_, _, band) in zip(bp["boxes"], nonempty):
        patch.set_facecolor(BAND_COLORS[band])
        patch.set_alpha(0.34)

    # Add a modest raw-value strip. Sampling prevents overplotting while preserving the distribution shape.
    for pos, vals, band in nonempty:
        arr = np.array(vals, dtype=float)
        if len(arr) > spec.get("max_points", 450):
            arr = rng.choice(arr, size=spec.get("max_points", 450), replace=False)
        jitter = rng.normal(loc=0.0, scale=0.055, size=len(arr))
        ax.scatter(
            arr,
            np.full(len(arr), pos) + jitter,
            s=7,
            alpha=0.18,
            color=BAND_COLORS[band],
            linewidths=0,
            rasterized=True,
        )

    for value in spec.get("thresholds", []):
        ax.axvline(value, color=THRESHOLD, linewidth=0.9, linestyle=(0, (3, 3)), alpha=0.72, zorder=0)

    ax.set_yticks(positions)
    ax.set_yticklabels([f"{band}\n(n={sum(1 for r in rows if r.get('final_band') == band):,})" for band in BAND_ORDER], fontsize=8.6, fontweight="bold")
    ax.set_ylim(0.35, len(BAND_ORDER) + 0.65)
    ax.set_title(spec["title"], loc="left", fontsize=11.2, fontweight="bold", color=TEXT_DARK, pad=6)
    ax.set_xlabel(spec["xlabel"], fontsize=9.2, fontweight="bold", color=TEXT_DARK, labelpad=5)

    if spec.get("scale"):
        ax.set_xscale(spec["scale"])
    if spec.get("xlim"):
        ax.set_xlim(spec["xlim"])
    if spec.get("xticks"):
        ax.set_xticks(spec["xticks"])
        ax.set_xticklabels([str(x) for x in spec["xticks"]])

    ax.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.85)
    ax.set_axisbelow(True)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#c7d0d3")
    ax.tick_params(axis="x", labelsize=8.3, colors=TEXT_DARK, width=0.7, length=3)
    ax.tick_params(axis="y", length=0, colors=TEXT_DARK)
    for tick in ax.get_xticklabels():
        tick.set_fontweight("bold")


def missingness_bin(value: float | None) -> str:
    if value is None:
        return "No metric"
    if value == 0:
        return "0%"
    if value <= 5:
        return ">0-5%"
    if value <= 20:
        return ">5-20%"
    if value <= 50:
        return ">20-50%"
    if value <= 80:
        return ">50-80%"
    return ">80%"


MISSINGNESS_BIN_ORDER = ["0%", ">0-5%", ">5-20%", ">20-50%", ">50-80%", ">80%", "No metric"]
MISSINGNESS_BIN_COLORS = {
    "0%": "#1b9e77",
    ">0-5%": "#7fc97f",
    ">5-20%": "#beaed4",
    ">20-50%": "#fdc086",
    ">50-80%": "#fdae61",
    ">80%": "#d95f02",
    "No metric": "#bdbdbd",
}


def plot_missingness_threshold_panel(
    ax: plt.Axes,
    rows: list[dict[str, Any]],
    spec: dict[str, Any],
) -> None:
    positions = np.arange(len(BAND_ORDER), 0, -1)
    counts_by_band: dict[str, dict[str, int]] = {}
    for band in BAND_ORDER:
        band_rows = [row for row in rows if row.get("final_band") == band]
        counts_by_band[band] = {label: 0 for label in MISSINGNESS_BIN_ORDER}
        for row in band_rows:
            counts_by_band[band][missingness_bin(as_num(row.get(spec["key"])))] += 1

    for pos, band in zip(positions, BAND_ORDER):
        total = sum(counts_by_band[band].values())
        left = 0.0
        for label in MISSINGNESS_BIN_ORDER:
            count = counts_by_band[band][label]
            if not count or not total:
                continue
            width = 100.0 * count / total
            ax.barh(
                pos,
                width,
                left=left,
                height=0.58,
                color=MISSINGNESS_BIN_COLORS[label],
                edgecolor="white",
                linewidth=0.7,
                alpha=0.9,
            )
            if width >= 10:
                ax.text(
                    left + width / 2,
                    pos,
                    f"{count:,}\n{width:.0f}%",
                    ha="center",
                    va="center",
                    fontsize=6.9,
                    fontweight="bold",
                    color="white" if label in {">50-80%", ">80%"} else TEXT_DARK,
                )
            left += width

    ax.axvline(50, color=THRESHOLD, linewidth=0.9, linestyle=(0, (3, 3)), alpha=0.72, zorder=0)
    ax.axvline(80, color=THRESHOLD, linewidth=0.9, linestyle=(0, (3, 3)), alpha=0.72, zorder=0)
    ax.set_xlim(0, 100)
    ax.set_yticks(positions)
    ax.set_yticklabels([f"{band}\n(n={sum(1 for r in rows if r.get('final_band') == band):,})" for band in BAND_ORDER], fontsize=8.6, fontweight="bold")
    ax.set_ylim(0.35, len(BAND_ORDER) + 0.65)
    ax.set_title("Sample-level missingness", loc="left", fontsize=11.2, fontweight="bold", color=TEXT_DARK, pad=6)
    ax.set_xlabel("Studies in each missingness category (%)", fontsize=9.2, fontweight="bold", color=TEXT_DARK, labelpad=5)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.85)
    ax.set_axisbelow(True)
    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#c7d0d3")
    ax.tick_params(axis="x", labelsize=8.3, colors=TEXT_DARK, width=0.7, length=3)
    ax.tick_params(axis="y", length=0, colors=TEXT_DARK)
    for tick in ax.get_xticklabels():
        tick.set_fontweight("bold")

    handles = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=MISSINGNESS_BIN_COLORS[label], markeredgecolor="none", markersize=5.3, label=label)
        for label in MISSINGNESS_BIN_ORDER
        if any(counts_by_band[band][label] for band in BAND_ORDER)
    ]
    ax.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncol=4,
        frameon=False,
        prop={"weight": "bold", "size": 5.9},
        handlelength=0.9,
        columnspacing=0.7,
    )


def make_figure(rows: list[dict[str, Any]], specs: list[dict[str, Any]], out_dir: Path, stem: str, title: str) -> None:
    rng = np.random.default_rng(20260501)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.linewidth": 0.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    n = len(specs)
    ncols = 2
    nrows = math.ceil(n / ncols)
    if n <= 4:
        figsize = (10.8, 6.65)
        top = 0.84
        bottom = 0.11
        hspace = 0.66
        legend_y = 0.955
        title_y = 0.988
        title_size = 14.2
        footnote_y = 0.014
        legend_size = 8.3
    else:
        figsize = (11.2, 3.0 * nrows)
        top = 0.91
        bottom = 0.075
        hspace = 0.58
        legend_y = 0.985
        title_y = 0.995
        title_size = 15.5
        footnote_y = 0.012
        legend_size = 8.8
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize, constrained_layout=False)
    axes_arr = np.array(axes).reshape(-1)
    for ax, spec in zip(axes_arr, specs):
        plot_panel(ax, rows, spec, rng)
    for ax in axes_arr[len(specs):]:
        ax.axis("off")

    handles = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=BAND_COLORS[b], markeredgecolor="none", markersize=8, alpha=0.75, label=b)
        for b in BAND_ORDER
    ]
    handles.append(Line2D([0], [0], color=THRESHOLD, linestyle=(0, (3, 3)), linewidth=1.0, label="MERIT guide threshold"))
    fig.legend(
        handles=handles,
        loc="upper right",
        bbox_to_anchor=(0.985, legend_y),
        ncol=3,
        frameon=False,
        fontsize=legend_size,
        prop={"weight": "bold", "size": legend_size},
        handlelength=1.2,
        columnspacing=1.2,
    )
    fig.suptitle(title, x=0.02, y=title_y, ha="left", fontsize=title_size, fontweight="bold", color=TEXT_DARK)
    fig.text(
        0.02,
        footnote_y,
        "Study-level v7 records grouped by final readiness band. Points are raw study values; boxes show median, IQR, and 5th-95th percentile whiskers.",
        ha="left",
        va="bottom",
        fontsize=8.8,
        fontweight="bold",
        color=TEXT_MUTED,
    )
    fig.subplots_adjust(left=0.125, right=0.985, top=top, bottom=bottom, wspace=0.25, hspace=hspace)
    for ext in ["pdf", "svg"]:
        fig.savefig(out_dir / f"{stem}.{ext}", bbox_inches="tight")
    fig.savefig(out_dir / f"{stem}.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def make_core4_figure(rows: list[dict[str, Any]], specs: list[dict[str, Any]], out_dir: Path) -> None:
    rng = np.random.default_rng(20260501)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.linewidth": 0.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig = plt.figure(figsize=(10.8, 6.65), constrained_layout=False)
    gs = fig.add_gridspec(2, 2)

    axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1]), fig.add_subplot(gs[1, 0])]
    for ax, spec in zip(axes, specs[:3]):
        plot_panel(ax, rows, spec, rng)
    ax_missingness = fig.add_subplot(gs[1, 1])
    plot_missingness_threshold_panel(ax_missingness, rows, specs[3])

    handles = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=BAND_COLORS[b], markeredgecolor="none", markersize=8, alpha=0.75, label=b)
        for b in BAND_ORDER
    ]
    handles.append(Line2D([0], [0], color=THRESHOLD, linestyle=(0, (3, 3)), linewidth=1.0, label="MERIT guide threshold"))
    fig.legend(
        handles=handles,
        loc="upper right",
        bbox_to_anchor=(0.985, 0.955),
        ncol=3,
        frameon=False,
        prop={"weight": "bold", "size": 8.3},
        handlelength=1.2,
        columnspacing=1.2,
    )
    fig.suptitle(
        "Core raw ML constraints by final readiness band",
        x=0.02,
        y=0.988,
        ha="left",
        fontsize=14.2,
        fontweight="bold",
        color=TEXT_DARK,
    )
    fig.text(
        0.02,
        0.014,
        "Study-level v7 records grouped by final readiness band. First three panels show raw values with median/IQR boxes; missingness is binned by threshold category.",
        ha="left",
        va="bottom",
        fontsize=8.8,
        fontweight="bold",
        color=TEXT_MUTED,
    )
    fig.subplots_adjust(left=0.125, right=0.985, top=0.84, bottom=0.11, wspace=0.25, hspace=0.66)

    stem = "figure5_raw_ml_constraints_core4_main_plot"
    for ext in ["pdf", "svg"]:
        fig.savefig(out_dir / f"{stem}.{ext}", bbox_inches="tight")
    fig.savefig(out_dir / f"{stem}.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", default="merit-cache-workbench-full-v7")
    parser.add_argument(
        "--out-dir",
        default="merit/manuscript/figures/Figure-5/latest/raw_ml_metric_distributions_by_final_band_v7",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    main_specs = [
        {"key": "n_biological_samples", "title": "Biological sample count", "xlabel": "Biological samples", "scale": "symlog", "xlim": (0, 12000), "thresholds": [10, 20]},
        {"key": "min_class_count", "title": "Smallest class size", "xlabel": "Minimum samples per class", "scale": "symlog", "xlim": (0, 1200), "thresholds": [3, 5]},
        {"key": "n_classes", "title": "Number of deposited classes", "xlabel": "Distinct label groups", "scale": "symlog", "xlim": (0, 600), "thresholds": [2]},
        {"key": "n_features_total_all_matrices", "title": "Feature count", "xlabel": "Total features across matrices", "scale": "symlog", "xlim": (0, 500000), "thresholds": []},
        {"key": "median_pn_ratio", "title": "Feature/sample ratio", "xlabel": "Median p/n ratio", "scale": "symlog", "xlim": (0, 30000), "thresholds": [10, 50, 200]},
        {"key": "median_sample_missingness_pct", "title": "Sample-level missingness", "xlabel": "Median sample missingness (%)", "xlim": (-2, 102), "thresholds": [50, 80]},
        {"key": "pct_features_over_30pct_missingness", "title": "High-missingness feature burden", "xlabel": "Features >30% missingness (%)", "xlim": (-2, 102), "thresholds": [10]},
        {"key": "class_balance_min_over_max", "title": "Class imbalance", "xlabel": "Min/max class-size ratio", "xlim": (-0.03, 1.03), "thresholds": [0.25, 0.5]},
    ]
    secondary_specs = [
        {"key": "n_total_samples", "title": "Total deposited sample count", "xlabel": "Total samples", "scale": "symlog", "xlim": (0, 14000), "thresholds": []},
        {"key": "n_feature_matrices", "title": "Usable matrix count", "xlabel": "Usable feature matrices", "scale": "symlog", "xlim": (0, 80), "thresholds": [1]},
        {"key": "worst_pn_ratio", "title": "Worst feature/sample ratio", "xlabel": "Worst p/n ratio", "scale": "symlog", "xlim": (0, 50000), "thresholds": [10, 50, 200]},
        {"key": "class_dependent_missingness_gap_pct", "title": "Class-dependent missingness", "xlabel": "Weighted class missingness gap (%)", "xlim": (-2, 102), "thresholds": []},
        {"key": "sample_outlier_rate_pct", "title": "Sample outlier burden", "xlabel": "Sample outlier rate (%)", "xlim": (-2, 102), "thresholds": []},
        {"key": "high_corr_pair_rate_pct", "title": "Feature redundancy burden", "xlabel": "Highly correlated sampled pairs (%)", "scale": "symlog", "xlim": (0, 105), "thresholds": []},
        {"key": "label_entropy_norm", "title": "Label entropy", "xlabel": "Normalized label entropy", "xlim": (-0.03, 1.03), "thresholds": []},
        {"key": "label_coverage_pct", "title": "Label coverage", "xlabel": "Biological samples with usable label (%)", "xlim": (-2, 102), "thresholds": [80]},
    ]
    core4_specs = [
        {"key": "n_biological_samples", "title": "Biological sample count", "xlabel": "Biological samples", "scale": "symlog", "xlim": (0, 12000), "thresholds": [10, 20]},
        {"key": "min_class_count", "title": "Smallest class size", "xlabel": "Minimum samples per class", "scale": "symlog", "xlim": (0, 1200), "thresholds": [3, 5]},
        {"key": "median_pn_ratio", "title": "Feature/sample ratio", "xlabel": "Median p/n ratio", "scale": "symlog", "xlim": (0, 30000), "thresholds": [10, 50, 200]},
        {"key": "median_sample_missingness_pct", "title": "Sample-level missingness", "xlabel": "Median sample missingness (%)", "xlim": (-2, 102), "thresholds": [50, 80]},
    ]

    rows = load_rows(Path(args.cache_root))
    fieldnames = [
        "study_id", "final_band", "study_score", "primary_source", "workflow_state_path",
        "gate_ceiling", "provisional_band", "G1_status", "G1_value", "G2_status", "G2_value",
        "G3_status", "G3_value", "G4_status", "G4_value", "G5_status", "G5_value",
        "n_biological_samples", "n_total_samples", "minimum_sample_threshold", "n_feature_matrices",
        "n_matrices_total", "n_classes", "min_class_count", "max_class_count",
        "class_balance_min_over_max", "label_entropy_norm", "distinct_label_groups", "label_coverage_pct",
        "n_features_total_all_matrices", "median_pn_ratio", "worst_pn_ratio", "pct_analyses_pn_gt1",
        "median_sample_missingness_pct", "mean_sample_missingness_pct", "class_dependent_missingness_gap_pct",
        "mean_feature_missingness_pct", "median_feature_missingness_pct", "pct_features_over_30pct_missingness",
        "n_high_missing_features", "sample_outlier_rate_pct", "sample_outliers", "sample_outlier_denominator",
        "high_corr_pair_rate_pct", "high_correlation_pairs", "sampled_correlation_pairs",
    ]
    write_tsv(out_dir / "raw_ml_metrics_by_final_band_study_level.tsv", rows, fieldnames)
    summary = summarize(rows, main_specs + secondary_specs)
    write_tsv(out_dir / "raw_ml_metric_band_summary.tsv", summary)

    make_figure(rows, main_specs, out_dir, "figure5_raw_ml_constraints_by_final_band_main", "Raw ML constraints by final readiness band")
    make_figure(rows, secondary_specs, out_dir, "figure5_raw_ml_diagnostics_by_final_band_secondary", "Secondary raw diagnostics by final readiness band")
    make_core4_figure(rows, core4_specs, out_dir)

    with (out_dir / "README.md").open("w") as fh:
        fh.write("# Raw ML Metric Distributions by Final Readiness Band\n\n")
        fh.write(f"Source cache: `{args.cache_root}`\n\n")
        fh.write("Unit: one study-level v7 workflow state from `index.json` (`n=4,121`).\n\n")
        fh.write("The figures show raw quantities, not normalized MERIT metric scores.\n\n")
        fh.write("Main figure panels: biological sample count, smallest class size, number of classes, feature count, p/n ratio, sample-level missingness, feature-level missingness burden, and class imbalance.\n\n")
        fh.write("Secondary panels: total samples, usable matrices, worst p/n ratio, class-dependent missingness, sample outliers, feature redundancy, label entropy, and label coverage.\n")
        fh.write("\nFocused main plot: biological sample count, smallest class size, p/n ratio, and median sample-level missingness.\n")

    print(f"Wrote raw metric plot set to {out_dir}")
    print("Study counts by final band:")
    for band in BAND_ORDER:
        print(f"{band}\t{sum(1 for r in rows if r.get('final_band') == band)}")


if __name__ == "__main__":
    main()
