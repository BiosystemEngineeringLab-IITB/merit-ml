#!/usr/bin/env python3
"""Generate manuscript-ready readiness summary figures (main + supplementary).

Outputs:
  - figureR1_readiness_landscape.{png,pdf,svg}
  - figureS_R1_source_diagnostics.{png,pdf,svg}
  - figureS_R2_robustness_denominator.{png,pdf,svg}
  - figureR1_caption.md
  - figureS_readiness_captions.md
  - readiness_plot_stats.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd


# Typography/embedding for manuscript output consistency.
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["pdf.fonttype"] = 42
plt.rcParams["ps.fonttype"] = 42

BAND_ORDER = ["Ready", "Conditional", "Fragile", "Not Ready", "No Data"]
SOURCE_ORDER = ["datatable", "mwtab", "untarg_data"]
DIM_ORDER = ["structural", "metadata", "analytical", "annotation", "cohort", "ml_feasibility"]

BAND_COLORS = {
    "Ready": "#1b9e77",
    "Conditional": "#66a61e",
    "Fragile": "#e6ab02",
    "Not Ready": "#d95f02",
    "No Data": "#7570b3",
}
SOURCE_COLORS = {"datatable": "#1f77b4", "mwtab": "#2ca02c", "untarg_data": "#ff7f0e"}
CONF_COLORS = {"Low": "#b22222", "Moderate": "#c77c02", "High": "#1f8a4c"}

REQUIRED_INPUTS = [
    "readiness_analysis_long.tsv",
    "overall_band_distribution.tsv",
    "source_band_mix.tsv",
    "study_level_spread.tsv",
    "dimension_mean_scores.tsv",
    "dimension_bottleneck_summary.tsv",
    "score_confidence_distribution.tsv",
    "source_dimension_summary.tsv",
    "source_dimension_deltas.tsv",
    "platform_band_crosstab.tsv",
    "platform_band_association.tsv",
]


def _panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(
        -0.12,
        1.08,
        label,
        transform=ax.transAxes,
        fontsize=14,
        fontweight="bold",
        va="bottom",
        ha="left",
    )


def _pretty_dim(name: str) -> str:
    return {
        "ml_feasibility": "ML Feasibility",
        "structural": "Structural",
        "metadata": "Metadata",
        "analytical": "Analytical",
        "annotation": "Annotation",
        "cohort": "Cohort",
    }.get(name, name.replace("_", " ").title())


def _stable_seed(base_seed: int, label: str) -> int:
    digest = hashlib.md5(label.encode("utf-8")).hexdigest()
    return (base_seed + int(digest[:8], 16)) % (2**32 - 1)


def _bootstrap_median_ci(values: np.ndarray, n_boot: int, base_seed: int, label: str) -> tuple[float, float, float]:
    vals = np.asarray(values, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return (math.nan, math.nan, math.nan)
    med = float(np.median(vals))
    rng = np.random.default_rng(_stable_seed(base_seed, label))
    n = vals.size
    boot = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        idx = rng.integers(0, n, n)
        boot[i] = float(np.median(vals[idx]))
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return (med, float(lo), float(hi))


def _cliffs_delta(x: np.ndarray, y: np.ndarray) -> float:
    xv = np.asarray(x, dtype=float)
    yv = np.asarray(y, dtype=float)
    xv = xv[np.isfinite(xv)]
    yv = yv[np.isfinite(yv)]
    if xv.size == 0 or yv.size == 0:
        return math.nan
    ys = np.sort(yv)
    n = xv.size
    m = ys.size
    gt = np.searchsorted(ys, xv, side="left").sum()  # count(y < x)
    lt = (m - np.searchsorted(ys, xv, side="right")).sum()  # count(y > x)
    return float((gt - lt) / (n * m))


def _save_multi(fig: plt.Figure, out_base: Path, formats: list[str], dpi: int = 300) -> None:
    for ext in formats:
        ext = ext.lower().strip()
        if ext not in {"png", "pdf", "svg"}:
            raise ValueError(f"Unsupported format: {ext}")
        fig.savefig(out_base.with_suffix(f".{ext}"), dpi=dpi, bbox_inches="tight")


def _load_inputs(input_dir: Path) -> dict[str, pd.DataFrame]:
    missing = [f for f in REQUIRED_INPUTS if not (input_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing required inputs in {input_dir}: {missing}")
    out: dict[str, pd.DataFrame] = {}
    for f in REQUIRED_INPUTS:
        out[f] = pd.read_csv(input_dir / f, sep="\t")
    return out


def _validate_integrity(d: dict[str, pd.DataFrame]) -> None:
    overall = d["overall_band_distribution.tsv"]
    pct_sum = float(overall["percent"].sum())
    if abs(pct_sum - 100.0) > 0.01:
        raise ValueError(f"overall_band_distribution percent sum must be 100±0.01, got {pct_sum:.6f}")

    long_df = d["readiness_analysis_long.tsv"]
    source_mix = d["source_band_mix.tsv"]
    long_counts = (
        long_df[long_df["source"].isin(SOURCE_ORDER)]
        .groupby("source", dropna=False)
        .size()
        .reindex(SOURCE_ORDER, fill_value=0)
    )
    mix_counts = (
        source_mix.set_index("source")["total"].reindex(SOURCE_ORDER).fillna(0).astype(int)
    )
    if not np.array_equal(long_counts.values.astype(int), mix_counts.values.astype(int)):
        raise ValueError(
            "source_band_mix totals do not match readiness_analysis_long counts.\n"
            f"long={long_counts.to_dict()}, mix={mix_counts.to_dict()}"
        )


def _compute_stats(d: dict[str, pd.DataFrame], seed: int, n_boot: int = 10_000) -> pd.DataFrame:
    long_df = d["readiness_analysis_long.tsv"].copy()
    analysis_df = long_df[long_df["source"].isin(SOURCE_ORDER) & long_df["score"].notna()].copy()

    records: list[dict[str, Any]] = []

    def add(
        figure: str,
        panel: str,
        metric: str,
        group: str,
        value: float | int | str,
        n: int | None = None,
        ci_low: float | None = None,
        ci_high: float | None = None,
        comparison: str | None = None,
        note: str | None = None,
    ) -> None:
        records.append(
            {
                "figure": figure,
                "panel": panel,
                "metric": metric,
                "group": group,
                "comparison": comparison or "",
                "value": value,
                "ci_low": ci_low if ci_low is not None else "",
                "ci_high": ci_high if ci_high is not None else "",
                "n": n if n is not None else "",
                "note": note or "",
            }
        )

    # Main panel A: overall score summary.
    scores_all = analysis_df["score"].to_numpy()
    med, lo, hi = _bootstrap_median_ci(scores_all, n_boot, seed, "overall_score")
    add("R1", "A", "score_median", "overall", med, n=len(scores_all), ci_low=lo, ci_high=hi)
    q1, q3 = np.percentile(scores_all, [25, 75])
    add("R1", "A", "score_q1", "overall", float(q1), n=len(scores_all))
    add("R1", "A", "score_q3", "overall", float(q3), n=len(scores_all))
    for _, r in d["overall_band_distribution.tsv"].iterrows():
        add("R1", "A", "band_percent", str(r["band"]), float(r["percent"]), n=int(r["count"]))

    # Main panel B: source medians + Cliff's delta.
    for src in SOURCE_ORDER:
        vals = analysis_df.loc[analysis_df["source"] == src, "score"].to_numpy()
        med, lo, hi = _bootstrap_median_ci(vals, n_boot, seed, f"source_{src}_score")
        add("R1", "B", "score_median", src, med, n=len(vals), ci_low=lo, ci_high=hi)
    pairs = [("datatable", "mwtab"), ("datatable", "untarg_data"), ("mwtab", "untarg_data")]
    for a, b in pairs:
        va = analysis_df.loc[analysis_df["source"] == a, "score"].to_numpy()
        vb = analysis_df.loc[analysis_df["source"] == b, "score"].to_numpy()
        add(
            "R1",
            "B",
            "cliffs_delta",
            f"{a}_vs_{b}",
            _cliffs_delta(va, vb),
            n=len(va) + len(vb),
            comparison=f"{a} vs {b}",
        )

    # Main panel C: spread stats.
    spread = d["study_level_spread.tsv"].copy()
    multi = spread[spread["n_unique_analyses"] > 1]
    if not multi.empty:
        med_r, lo_r, hi_r = _bootstrap_median_ci(multi["score_range"].to_numpy(), n_boot, seed, "study_score_range")
        add("R1", "C", "study_score_range_median", "multi_analysis_studies", med_r, n=len(multi), ci_low=lo_r, ci_high=hi_r)
    top20 = spread.sort_values("score_range", ascending=False).head(20)
    add("R1", "C", "top20_max_score_range", "top20_studies", float(top20["score_range"].max()), n=len(top20))

    # Main panel D: bottleneck.
    bott = d["dimension_bottleneck_summary.tsv"].copy()
    first = bott.iloc[0]
    add("R1", "D", "global_bottleneck_dimension", "overall", str(first["global_bottleneck_dimension"]))
    add("R1", "D", "global_bottleneck_mean_score", str(first["global_bottleneck_dimension"]), float(first["global_bottleneck_mean_score"]))

    # Main panel E: confidence distribution.
    for _, r in d["score_confidence_distribution.tsv"].iterrows():
        add("R1", "E", "confidence_percent", str(r["confidence"]), float(r["percent"]), n=int(r["count"]))

    # Supplementary R1C: platform-band association.
    assoc = d["platform_band_association.tsv"].iloc[0]
    add("S-R1", "C", "cramers_v", "platform_vs_band", float(assoc["cramers_v"]), n=int(assoc["n_rows_used"]))

    # Supplementary R2B: sensitivity medians (analysis-level vs study-collapsed).
    # Overall
    analysis_med, a_lo, a_hi = _bootstrap_median_ci(analysis_df["score"].to_numpy(), n_boot, seed, "sens_overall_analysis")
    study_overall = analysis_df.groupby("study_id", dropna=False)["score"].median().to_numpy()
    study_med, s_lo, s_hi = _bootstrap_median_ci(study_overall, n_boot, seed, "sens_overall_study")
    add("S-R2", "B", "analysis_level_median", "overall", analysis_med, n=len(analysis_df), ci_low=a_lo, ci_high=a_hi)
    add("S-R2", "B", "study_collapsed_median", "overall", study_med, n=len(study_overall), ci_low=s_lo, ci_high=s_hi)
    add("S-R2", "B", "median_delta_analysis_minus_study", "overall", analysis_med - study_med, n=len(study_overall))
    for src in SOURCE_ORDER:
        src_df = analysis_df[analysis_df["source"] == src]
        analysis_med, a_lo, a_hi = _bootstrap_median_ci(src_df["score"].to_numpy(), n_boot, seed, f"sens_{src}_analysis")
        study_medians = src_df.groupby("study_id", dropna=False)["score"].median().to_numpy()
        study_med, s_lo, s_hi = _bootstrap_median_ci(study_medians, n_boot, seed, f"sens_{src}_study")
        add("S-R2", "B", "analysis_level_median", src, analysis_med, n=len(src_df), ci_low=a_lo, ci_high=a_hi)
        add("S-R2", "B", "study_collapsed_median", src, study_med, n=len(study_medians), ci_low=s_lo, ci_high=s_hi)
        add("S-R2", "B", "median_delta_analysis_minus_study", src, analysis_med - study_med, n=len(study_medians))

    stats = pd.DataFrame(records)
    return stats


def _plot_main_r1(d: dict[str, pd.DataFrame], stats: pd.DataFrame) -> plt.Figure:
    long_df = d["readiness_analysis_long.tsv"].copy()
    analysis_df = long_df[long_df["source"].isin(SOURCE_ORDER) & long_df["score"].notna()].copy()
    overall_band = d["overall_band_distribution.tsv"].copy()
    source_mix = d["source_band_mix.tsv"].copy().set_index("source").reindex(SOURCE_ORDER).reset_index()
    spread = d["study_level_spread.tsv"].copy()
    dim_means = d["dimension_mean_scores.tsv"].copy().set_index("dimension").reindex(DIM_ORDER).reset_index()
    conf = d["score_confidence_distribution.tsv"].copy().set_index("confidence").reindex(["Low", "Moderate", "High"]).reset_index()

    fig = plt.figure(figsize=(18, 15))
    gs = fig.add_gridspec(3, 2, height_ratios=[1.0, 1.1, 1.0], hspace=0.35, wspace=0.25)

    # A: overall composition + histogram/ECDF
    subA = gs[0, 0].subgridspec(1, 2, width_ratios=[1.1, 1.6], wspace=0.35)
    axA1 = fig.add_subplot(subA[0, 0])
    axA2 = fig.add_subplot(subA[0, 1])
    _panel_label(axA1, "A")

    left = 0.0
    for _, r in overall_band.set_index("band").reindex(BAND_ORDER).reset_index().iterrows():
        axA1.barh([0], [r["percent"]], left=left, color=BAND_COLORS[str(r["band"])], edgecolor="white", height=0.6)
        if r["percent"] >= 6:
            axA1.text(left + r["percent"] / 2, 0, f'{int(r["count"])}\n({r["percent"]:.1f}%)', ha="center", va="center", fontsize=8, color="white", fontweight="bold")
        left += r["percent"]
    axA1.set_xlim(0, 100)
    axA1.set_ylim(-0.8, 0.8)
    axA1.set_yticks([])
    axA1.set_xlabel("Analyses (%)", fontsize=10, fontweight="bold")
    axA1.set_title("Readiness band composition", fontsize=11, fontweight="bold")
    axA1.spines["top"].set_visible(False)
    axA1.spines["right"].set_visible(False)
    legend_handles = [plt.Rectangle((0, 0), 1, 1, color=BAND_COLORS[b]) for b in BAND_ORDER]
    axA1.legend(legend_handles, BAND_ORDER, loc="lower center", bbox_to_anchor=(0.5, -0.42), ncol=2, frameon=False, fontsize=8)

    bins = np.linspace(0, 1, 26)
    axA2.hist(analysis_df["score"], bins=bins, color="#6b8fd6", alpha=0.8, edgecolor="white")
    axA2.set_xlim(0, 1)
    axA2.set_xlabel("ReadinessScore (0-1)", fontsize=10, fontweight="bold")
    axA2.set_ylabel("Analysis count", fontsize=10, fontweight="bold")
    axA2.set_title("Score distribution + ECDF", fontsize=11, fontweight="bold")
    axA2.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axA2.spines["top"].set_visible(False)
    axA2.spines["right"].set_visible(False)
    xs = np.sort(analysis_df["score"].to_numpy())
    ys = np.arange(1, len(xs) + 1) / len(xs)
    axA2b = axA2.twinx()
    axA2b.plot(xs, ys, color="#1d4f91", linewidth=2)
    axA2b.set_ylim(0, 1)
    axA2b.set_ylabel("ECDF", fontsize=10, color="#1d4f91")
    axA2b.tick_params(axis="y", labelcolor="#1d4f91")
    med = stats[(stats["figure"] == "R1") & (stats["panel"] == "A") & (stats["metric"] == "score_median") & (stats["group"] == "overall")].iloc[0]
    q1 = float(stats[(stats["figure"] == "R1") & (stats["panel"] == "A") & (stats["metric"] == "score_q1")]["value"].iloc[0])
    q3 = float(stats[(stats["figure"] == "R1") & (stats["panel"] == "A") & (stats["metric"] == "score_q3")]["value"].iloc[0])
    axA2.text(
        0.02,
        0.95,
        f"Median={float(med['value']):.3f}\nIQR={q1:.3f}-{q3:.3f}",
        transform=axA2.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#bfbfbf", "alpha": 0.9},
    )

    # B: source stratification (violin + source band stacked)
    subB = gs[0, 1].subgridspec(1, 2, width_ratios=[1.3, 1.1], wspace=0.35)
    axB1 = fig.add_subplot(subB[0, 0])
    axB2 = fig.add_subplot(subB[0, 1])
    _panel_label(axB1, "B")

    violin_data = [analysis_df.loc[analysis_df["source"] == s, "score"].to_numpy() for s in SOURCE_ORDER]
    parts = axB1.violinplot(violin_data, showmeans=False, showmedians=False, showextrema=False)
    for i, body in enumerate(parts["bodies"]):
        body.set_facecolor(SOURCE_COLORS[SOURCE_ORDER[i]])
        body.set_edgecolor("#303030")
        body.set_alpha(0.55)
    axB1.set_xticks([1, 2, 3])
    axB1.set_xticklabels(["datatable", "mwtab", "untarg_data"], rotation=12, ha="right")
    axB1.set_ylim(0, 1)
    axB1.set_ylabel("ReadinessScore", fontsize=10, fontweight="bold")
    axB1.set_title("Source-wise score distributions", fontsize=11, fontweight="bold")
    axB1.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axB1.spines["top"].set_visible(False)
    axB1.spines["right"].set_visible(False)
    for i, s in enumerate(SOURCE_ORDER, start=1):
        row = stats[(stats["figure"] == "R1") & (stats["panel"] == "B") & (stats["metric"] == "score_median") & (stats["group"] == s)].iloc[0]
        med_val = float(row["value"])
        lo = float(row["ci_low"])
        hi = float(row["ci_high"])
        axB1.plot([i, i], [lo, hi], color="#111111", linewidth=2.0, zorder=5)
        axB1.scatter([i], [med_val], color="#111111", s=28, zorder=6)
        axB1.text(i, min(0.985, hi + 0.04), f"med={med_val:.3f}", ha="center", va="bottom", fontsize=8)

    bottoms = np.zeros(len(SOURCE_ORDER))
    x = np.arange(len(SOURCE_ORDER))
    for b in BAND_ORDER:
        vals = source_mix[f"{b}_pct"].to_numpy(dtype=float)
        axB2.bar(x, vals, bottom=bottoms, color=BAND_COLORS[b], edgecolor="white", linewidth=0.7, label=b)
        bottoms += vals
    axB2.set_xticks(x)
    axB2.set_xticklabels(["datatable", "mwtab", "untarg_data"], rotation=12, ha="right")
    axB2.set_ylim(0, 100)
    axB2.set_ylabel("Within-source band share (%)", fontsize=10, fontweight="bold")
    axB2.set_title("Band composition by source", fontsize=11, fontweight="bold")
    axB2.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axB2.spines["top"].set_visible(False)
    axB2.spines["right"].set_visible(False)
    axB2.legend(loc="lower center", bbox_to_anchor=(0.5, -0.4), ncol=2, frameon=False, fontsize=8)

    # C: within-study spread + top20 heat strip
    subC = gs[1, :].subgridspec(1, 2, width_ratios=[1.3, 1.2], wspace=0.28)
    axC1 = fig.add_subplot(subC[0, 0])
    axC2 = fig.add_subplot(subC[0, 1])
    _panel_label(axC1, "C")

    spread_multi = spread[spread["n_unique_analyses"] > 1].copy()
    axC1.hist(spread_multi["score_range"], bins=30, color="#7ea9db", edgecolor="white", alpha=0.9)
    axC1.set_xlabel("Within-study score range (max-min)", fontsize=10, fontweight="bold")
    axC1.set_ylabel("Study count", fontsize=10, fontweight="bold")
    axC1.set_title("Spread across analyses within each study", fontsize=11, fontweight="bold")
    axC1.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axC1.spines["top"].set_visible(False)
    axC1.spines["right"].set_visible(False)
    if not spread_multi.empty:
        med_r = float(np.median(spread_multi["score_range"]))
        axC1.axvline(med_r, color="#1f4e79", linestyle="--", linewidth=1.8)
        axC1.text(med_r, axC1.get_ylim()[1] * 0.95, f"median={med_r:.3f}", ha="left", va="top", fontsize=9)

    top20 = spread.sort_values("score_range", ascending=False).head(20)["study_id"].tolist()
    score_lists = []
    for sid in top20:
        vals = (
            analysis_df.loc[analysis_df["study_id"] == sid, "score"]
            .sort_values()
            .to_numpy(dtype=float)
        )
        score_lists.append(vals)
    max_len = max((len(v) for v in score_lists), default=1)
    mat = np.full((len(score_lists), max_len), np.nan, dtype=float)
    for i, vals in enumerate(score_lists):
        mat[i, : len(vals)] = vals
    cmap = plt.cm.viridis.copy()
    cmap.set_bad("#e6e6e6")
    im = axC2.imshow(mat, aspect="auto", cmap=cmap, vmin=0, vmax=1, interpolation="nearest")
    axC2.set_title("Top-20 studies by score range (analysis rows)", fontsize=11, fontweight="bold")
    axC2.set_xlabel("Ordered analysis row index (low→high score)", fontsize=10)
    axC2.set_yticks(np.arange(len(top20)))
    axC2.set_yticklabels(top20, fontsize=8)
    cbar = fig.colorbar(im, ax=axC2, fraction=0.046, pad=0.02)
    cbar.set_label("ReadinessScore", fontsize=9)

    # D: dimension bottlenecks
    axD = fig.add_subplot(gs[2, 0])
    _panel_label(axD, "D")
    dm = dim_means.sort_values("mean_score", ascending=True).copy()
    y = np.arange(len(dm))
    axD.hlines(y, xmin=0, xmax=dm["mean_score"], color="#8c8c8c", linewidth=2)
    colors = ["#b22222" if i == 0 else "#4c78a8" for i in range(len(dm))]
    axD.scatter(dm["mean_score"], y, c=colors, s=70, zorder=3)
    for i, r in dm.reset_index(drop=True).iterrows():
        axD.text(float(r["mean_score"]) + 0.01, i, f"{float(r['mean_score']):.3f}", va="center", fontsize=9)
    axD.set_yticks(y)
    axD.set_yticklabels([_pretty_dim(x) for x in dm["dimension"]], fontsize=10)
    axD.set_xlim(0, 1)
    axD.set_xlabel("Mean dimension score", fontsize=10, fontweight="bold")
    axD.set_title("Dimension bottlenecks (corpus-wide means)", fontsize=11, fontweight="bold")
    axD.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
    axD.spines["top"].set_visible(False)
    axD.spines["right"].set_visible(False)

    # E: confidence distribution
    axE = fig.add_subplot(gs[2, 1])
    _panel_label(axE, "E")
    conf = conf.fillna(0)
    x = np.arange(len(conf))
    bars = axE.bar(x, conf["percent"], color=[CONF_COLORS[c] for c in conf["confidence"]], edgecolor="white", width=0.65)
    for i, (bar, (_, r)) in enumerate(zip(bars, conf.iterrows())):
        axE.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1.5,
            f"{int(r['count'])}\n({float(r['percent']):.1f}%)",
            ha="center",
            va="bottom",
            fontsize=8,
            fontweight="bold",
        )
    axE.set_xticks(x)
    axE.set_xticklabels(conf["confidence"].tolist(), fontsize=10)
    axE.set_ylim(0, max(100, conf["percent"].max() * 1.28))
    axE.set_ylabel("Analyses (%)", fontsize=10, fontweight="bold")
    axE.set_title("Score confidence distribution", fontsize=11, fontweight="bold")
    axE.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axE.spines["top"].set_visible(False)
    axE.spines["right"].set_visible(False)

    fig.suptitle("Figure R1. Repository Readiness Landscape", fontsize=15, fontweight="bold", y=1.01)
    return fig


def _plot_supp_sr1(d: dict[str, pd.DataFrame]) -> plt.Figure:
    source_dim = d["source_dimension_summary.tsv"].copy().set_index("source").reindex(SOURCE_ORDER)
    delta = d["source_dimension_deltas.tsv"].copy()
    platform = d["platform_band_crosstab.tsv"].copy()
    assoc = d["platform_band_association.tsv"].iloc[0]

    fig = plt.figure(figsize=(18, 6))
    gs = fig.add_gridspec(1, 3, wspace=0.35)
    axA = fig.add_subplot(gs[0, 0])
    axB = fig.add_subplot(gs[0, 1])
    axC = fig.add_subplot(gs[0, 2])
    _panel_label(axA, "A")
    _panel_label(axB, "B")
    _panel_label(axC, "C")

    # A: source x dimension heatmap.
    mat = source_dim[DIM_ORDER].to_numpy(dtype=float)
    im = axA.imshow(mat, cmap="YlGnBu", vmin=0, vmax=1, aspect="auto")
    axA.set_xticks(np.arange(len(DIM_ORDER)))
    axA.set_xticklabels([_pretty_dim(x) for x in DIM_ORDER], rotation=35, ha="right", fontsize=9)
    axA.set_yticks(np.arange(len(SOURCE_ORDER)))
    axA.set_yticklabels(SOURCE_ORDER, fontsize=10)
    axA.set_title("Source-wise dimension mean scores", fontsize=11, fontweight="bold")
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            axA.text(j, i, f"{mat[i, j]:.3f}", ha="center", va="center", fontsize=8, color="#0e2a43")
    cbar = fig.colorbar(im, ax=axA, fraction=0.048, pad=0.02)
    cbar.set_label("Mean score", fontsize=9)

    # B: source deltas per dimension.
    delta = delta.copy()
    delta["abs_spread"] = delta["spread_max_minus_min"].abs()
    delta = delta.sort_values("abs_spread", ascending=True).reset_index(drop=True)
    y = np.arange(len(delta))
    axB.axvline(0, color="#7f7f7f", linewidth=1.2, linestyle="--")
    axB.hlines(y, xmin=delta["datatable_minus_untarg_data"], xmax=delta["datatable_minus_mwtab"], color="#bdbdbd", linewidth=1.2)
    axB.scatter(delta["datatable_minus_untarg_data"], y, color="#1f77b4", s=48, label="datatable - untarg")
    axB.scatter(delta["mwtab_minus_untarg_data"], y, color="#2ca02c", s=48, label="mwtab - untarg")
    axB.scatter(delta["datatable_minus_mwtab"], y, color="#9467bd", s=48, label="datatable - mwtab")
    axB.set_yticks(y)
    axB.set_yticklabels([_pretty_dim(x) for x in delta["dimension"]], fontsize=9)
    axB.set_xlabel("Mean score delta", fontsize=10, fontweight="bold")
    axB.set_title("Source delta diagnostics (ranked by spread)", fontsize=11, fontweight="bold")
    axB.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
    axB.legend(frameon=False, fontsize=8, loc="lower right")
    axB.spines["top"].set_visible(False)
    axB.spines["right"].set_visible(False)

    # C: platform-band stacked bars with Cramer's V.
    p = platform.copy()
    p["total"] = p["total"].astype(float)
    for b in BAND_ORDER:
        p[f"{b}_pct"] = np.where(p["total"] > 0, 100.0 * p[b] / p["total"], 0.0)
    x = np.arange(len(p))
    bottom = np.zeros(len(p))
    for b in BAND_ORDER:
        vals = p[f"{b}_pct"].to_numpy(dtype=float)
        axC.bar(x, vals, bottom=bottom, color=BAND_COLORS[b], edgecolor="white", linewidth=0.7, label=b)
        bottom += vals
    axC.set_xticks(x)
    axC.set_xticklabels(p["platform_bucket"].tolist(), fontsize=10)
    axC.set_ylabel("Band share within platform bucket (%)", fontsize=10, fontweight="bold")
    axC.set_ylim(0, 100)
    axC.set_title("Platform bucket vs readiness band", fontsize=11, fontweight="bold")
    axC.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axC.spines["top"].set_visible(False)
    axC.spines["right"].set_visible(False)
    axC.text(
        0.03,
        0.97,
        f"Cramér's V = {float(assoc['cramers_v']):.4f}",
        transform=axC.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#bfbfbf", "alpha": 0.9},
    )
    axC.legend(frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, -0.35), ncol=2)

    fig.suptitle("Figure S-R1. Source Differences Diagnostics", fontsize=15, fontweight="bold", y=1.02)
    return fig


def _plot_supp_sr2(d: dict[str, pd.DataFrame], stats: pd.DataFrame) -> plt.Figure:
    long_df = d["readiness_analysis_long.tsv"].copy()
    analysis_df = long_df[long_df["source"].isin(SOURCE_ORDER) & long_df["score"].notna()].copy()
    counts_source = analysis_df["source"].value_counts().reindex(SOURCE_ORDER, fill_value=0)

    fig = plt.figure(figsize=(14.5, 6))
    gs = fig.add_gridspec(1, 2, wspace=0.35)
    axA = fig.add_subplot(gs[0, 0])
    axB = fig.add_subplot(gs[0, 1])
    _panel_label(axA, "A")
    _panel_label(axB, "B")

    # A: denominator transparency waterfall-like view.
    total_rows = len(long_df)
    source_filtered = int(long_df["source"].isin(SOURCE_ORDER).sum())
    scored_rows = int(long_df[long_df["source"].isin(SOURCE_ORDER) & long_df["score"].notna()].shape[0])
    stages = ["All rows", "Source-filtered", "Scored rows", "By source"]
    vals = [total_rows, source_filtered, scored_rows, scored_rows]
    colors = ["#9e9e9e", "#6c8fc7", "#355f9c", "#ffffff"]
    x = np.arange(len(stages))
    axA.bar(x[:3], vals[:3], color=colors[:3], edgecolor="white", width=0.68)
    # Final bar as decomposition.
    bottom = 0.0
    for s in SOURCE_ORDER:
        v = float(counts_source[s])
        axA.bar(x[3], v, bottom=bottom, color=SOURCE_COLORS[s], edgecolor="white", width=0.68)
        bottom += v
    for i in range(3):
        axA.plot([x[i] + 0.34, x[i + 1] - 0.34], [vals[i], vals[i + 1]], color="#7f7f7f", linewidth=1.0, linestyle="--")
    for i, v in enumerate(vals[:3]):
        axA.text(x[i], v + max(vals) * 0.02, f"{v:,}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    axA.text(x[3], vals[3] + max(vals) * 0.02, f"{vals[3]:,}", ha="center", va="bottom", fontsize=9, fontweight="bold")
    axA.set_xticks(x)
    axA.set_xticklabels(stages, rotation=12, ha="right")
    axA.set_ylabel("Analysis rows (n)", fontsize=10, fontweight="bold")
    axA.set_title("Analysis-count waterfall and source decomposition", fontsize=11, fontweight="bold")
    axA.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.5)
    axA.spines["top"].set_visible(False)
    axA.spines["right"].set_visible(False)
    source_handles = [plt.Rectangle((0, 0), 1, 1, color=SOURCE_COLORS[s]) for s in SOURCE_ORDER]
    axA.legend(source_handles, SOURCE_ORDER, frameon=False, fontsize=8, loc="upper right")

    # B: sensitivity (analysis-level vs study-collapsed medians).
    categories = ["overall", *SOURCE_ORDER]
    y = np.arange(len(categories))
    ana = []
    std = []
    for c in categories:
        ana_row = stats[
            (stats["figure"] == "S-R2")
            & (stats["panel"] == "B")
            & (stats["metric"] == "analysis_level_median")
            & (stats["group"] == c)
        ].iloc[0]
        std_row = stats[
            (stats["figure"] == "S-R2")
            & (stats["panel"] == "B")
            & (stats["metric"] == "study_collapsed_median")
            & (stats["group"] == c)
        ].iloc[0]
        ana.append(float(ana_row["value"]))
        std.append(float(std_row["value"]))
    for i, (a, s) in enumerate(zip(ana, std)):
        axB.plot([min(a, s), max(a, s)], [i, i], color="#bdbdbd", linewidth=2)
    axB.scatter(ana, y, color="#1f77b4", s=55, label="Analysis-level median", zorder=3)
    axB.scatter(std, y, color="#d95f02", s=55, label="Study-collapsed median", zorder=3)
    for i, c in enumerate(categories):
        axB.text(max(ana[i], std[i]) + 0.012, i, f"Δ={ana[i]-std[i]:+.3f}", va="center", fontsize=8)
    axB.set_yticks(y)
    axB.set_yticklabels(["Overall", "datatable", "mwtab", "untarg_data"])
    axB.set_xlim(0, 1)
    axB.set_xlabel("ReadinessScore median", fontsize=10, fontweight="bold")
    axB.set_title("Sensitivity: analysis-level vs study-collapsed medians", fontsize=11, fontweight="bold")
    axB.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.5)
    axB.spines["top"].set_visible(False)
    axB.spines["right"].set_visible(False)
    axB.legend(frameon=False, fontsize=8, loc="lower right")

    fig.suptitle("Figure S-R2. Robustness and Denominator Transparency", fontsize=15, fontweight="bold", y=1.02)
    return fig


def _write_captions(out_dir: Path, d: dict[str, pd.DataFrame]) -> None:
    long_df = d["readiness_analysis_long.tsv"]
    analysis_df = long_df[long_df["source"].isin(SOURCE_ORDER) & long_df["score"].notna()]
    n_analysis = len(analysis_df)
    n_studies = analysis_df["study_id"].nunique()

    r1 = f"""# Figure R1 Caption

**Figure R1. Repository readiness landscape (analysis-level).**  
Panel A shows overall readiness-band composition and score distribution (histogram with ECDF).  
Panel B shows source-stratified score distributions with median and bootstrap 95% CI, plus within-source band composition.  
Panel C summarizes within-study spread of readiness scores across analysis rows and highlights the top-20 highest-spread studies.  
Panel D ranks dimension means and marks the global bottleneck dimension.  
Panel E reports score-confidence distribution.

Denominators: **n={n_analysis:,} analysis rows** from **{n_studies:,} studies**.  
Primary unit is analysis-level rows from `readiness_analysis_long.tsv` (source-specific rows retained).  
Score confidence (Low/Moderate/High) follows MERIT UI logic based on informative dimensions, biological sample count, and metadata/analytical signal strength.
"""

    s = f"""# Supplementary Readiness Figures Captions

## Figure S-R1. Source Differences Diagnostics
Panel A: source-by-dimension heatmap of mean scores.  
Panel B: pairwise source deltas by dimension (`datatable−untarg`, `mwtab−untarg`, `datatable−mwtab`) ranked by spread.  
Panel C: platform-bucket band composition with Cramér's V effect size for platform-band association.

## Figure S-R2. Robustness and Denominator Transparency
Panel A: denominator cascade from all rows to source-filtered and scored subsets, with final source decomposition.  
Panel B: sensitivity comparison of medians using analysis-level rows versus study-collapsed medians (overall + by source).

All panels are generated from `merit/manuscript/supplementary/readinessscore_distribution/*.tsv` and are fully reproducible with fixed bootstrap seed.
"""

    (out_dir / "figureR1_caption.md").write_text(r1)
    (out_dir / "figureS_readiness_captions.md").write_text(s)


def _parse_formats(raw: str) -> list[str]:
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def run(args: argparse.Namespace) -> None:
    input_dir = Path(args.input_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    d = _load_inputs(input_dir)
    _validate_integrity(d)

    stats1 = _compute_stats(d, seed=args.seed, n_boot=args.n_boot)
    stats2 = _compute_stats(d, seed=args.seed, n_boot=args.n_boot)
    # Reproducibility check: stats table must match exactly for same seed.
    if not stats1.equals(stats2):
        raise RuntimeError("Reproducibility check failed: repeated stats computation differs with same seed.")

    stats_path = out_dir / "readiness_plot_stats.tsv"
    stats1.to_csv(stats_path, sep="\t", index=False)

    fmts = _parse_formats(args.formats)

    fig_r1 = _plot_main_r1(d, stats1)
    _save_multi(fig_r1, out_dir / "figureR1_readiness_landscape", fmts, dpi=args.dpi)
    plt.close(fig_r1)

    fig_s1 = _plot_supp_sr1(d)
    _save_multi(fig_s1, out_dir / "figureS_R1_source_diagnostics", fmts, dpi=args.dpi)
    plt.close(fig_s1)

    fig_s2 = _plot_supp_sr2(d, stats1)
    _save_multi(fig_s2, out_dir / "figureS_R2_robustness_denominator", fmts, dpi=args.dpi)
    plt.close(fig_s2)

    _write_captions(out_dir, d)
    print(f"Generated figures and stats in: {out_dir}")
    print(f"Stats rows: {len(stats1)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--input-dir",
        default="/home/shayantan/metabolomics/ML-ready/merit/manuscript/supplementary/readinessscore_distribution",
        help="Directory containing readinessscore_distribution TSV inputs.",
    )
    p.add_argument(
        "--out-dir",
        default="/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-5",
        help="Directory where figure and caption outputs are written.",
    )
    p.add_argument(
        "--formats",
        default="png,pdf,svg",
        help="Comma-separated output formats (png,pdf,svg).",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=20260411,
        help="Random seed for bootstrap reproducibility.",
    )
    p.add_argument(
        "--n-boot",
        type=int,
        default=10000,
        help="Bootstrap iterations for median confidence intervals.",
    )
    p.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="PNG export DPI.",
    )
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())

