#!/usr/bin/env python3
"""Sensitivity analysis for MERIT final bands under alternate gate thresholds.

This is a post hoc analysis over the local v7 workflow-state cache. It does not
recompute assessments. It reuses cached metric evidence, then varies only the
directly threshold-dependent quantities:

* biological sample threshold used by minimum_sample_count and G2
* minimum class threshold used by label_suitability and G4
* sample-level missingness gate kept at the current MERIT default: pass <=50%,
  warn <=80%, fail >80%
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from itertools import product
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

BAND_ORDER = ["Ready", "Conditional", "Fragile", "Not Ready", "No Data"]
BAND_COLORS = {
    "Ready": "#1b9e77",
    "Conditional": "#66a61e",
    "Fragile": "#e6ab02",
    "Not Ready": "#d95f02",
    "No Data": "#7570b3",
}
BAND_RANK = {band: rank for rank, band in enumerate(["No Data", "Not Ready", "Fragile", "Conditional", "Ready"])}
CORE_SECTION_KEYS = ("structural", "analytical", "annotation", "cohort", "ml_feasibility")
SECTION_SPECS = {
    "structural": ("schema_validation", 5, None),
    "analytical": ("analytical_readiness", 5, None),
    "annotation": ("annotation_readiness", 4, None),
    "cohort": ("cohort_bias", 3, None),
    "ml_feasibility": (
        "ml_readiness",
        4,
        {
            "disease_endpoint_extractability",
            "factor_label_harmonizability",
            "label_suitability",
            "feature_to_sample_ratio",
        },
    ),
}
TEXT_DARK = "#17252a"
TEXT_MUTED = "#52666d"
GRID = "#d8e0e2"
THRESHOLD = "#2f3f45"


def as_num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return None
        return out
    except Exception:
        return None


def band_from_score(score: float) -> str:
    if score >= 0.85:
        return "Ready"
    if score >= 0.7:
        return "Conditional"
    if score >= 0.5:
        return "Fragile"
    return "Not Ready"


def cap_band(provisional: str, ceiling: str | None) -> str:
    if not ceiling:
        return provisional
    capped_rank = min(BAND_RANK.get(provisional, 1), BAND_RANK.get(ceiling, 1))
    for band, rank in BAND_RANK.items():
        if rank == capped_rank:
            return band
    return provisional


def metric_by_name(report: dict[str, Any], section: str, name: str) -> dict[str, Any] | None:
    for metric in report.get(section, []) or []:
        if isinstance(metric, dict) and metric.get("name") == name:
            return metric
    return None


def section_score(
    report: dict[str, Any],
    section_key: str,
    overrides: dict[str, float] | None = None,
) -> float:
    section_name, fixed_count, include_names = SECTION_SPECS[section_key]
    metrics = []
    for metric in report.get(section_name, []) or []:
        if not isinstance(metric, dict):
            continue
        if metric.get("informational"):
            continue
        name = str(metric.get("name", ""))
        if include_names is not None and name not in include_names:
            continue
        metrics.append(metric)
    if not metrics:
        return 0.0
    denom = max(len(metrics), fixed_count)
    total = 0.0
    for metric in metrics:
        name = str(metric.get("name", ""))
        if overrides and name in overrides:
            total += overrides[name]
        else:
            total += float(as_num(metric.get("score")) or 0.0)
    return total / denom


def gate_dict(readiness_score: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for gate in readiness_score.get("gates", []) or []:
        if isinstance(gate, dict):
            out[str(gate.get("id"))] = gate
    return out


def gate_status_for_sample_count(n_bio: int, threshold: int) -> str:
    if n_bio >= threshold:
        return "pass"
    if n_bio >= max(5, threshold // 2):
        return "warn"
    return "fail"


def gate_status_for_min_class(min_class_n: int, n_groups: int, threshold: int) -> str:
    if n_groups >= 2 and min_class_n >= threshold:
        return "pass"
    if n_groups >= 2 and min_class_n >= 3:
        return "warn"
    return "fail"


def gate_status_for_missingness(value: float | None, pass_threshold: float, fail_threshold: float) -> str:
    if value is None:
        return "warn"
    if value <= pass_threshold:
        return "pass"
    if value <= fail_threshold:
        return "warn"
    return "fail"


def gate_ceiling(statuses: dict[str, str]) -> str | None:
    if statuses.get("G1") == "fail":
        return "No Data"
    if any(value == "fail" for value in statuses.values()):
        return "Not Ready"
    if any(value == "warn" for value in statuses.values()):
        return "Conditional"
    return None


def load_records(cache_root: Path) -> list[dict[str, Any]]:
    index = json.load((cache_root / "index.json").open())
    records = []
    for study_id, payload in sorted(index.get("studies", {}).items()):
        state = json.load((cache_root / payload["state_path"]).open())
        report = state.get("final_report") or {}
        rs = state.get("readiness_score") or {}
        gates = gate_dict(rs)

        min_sample_metric = metric_by_name(report, "schema_validation", "minimum_sample_count") or {}
        min_sample_details = min_sample_metric.get("details") or {}
        label_metric = metric_by_name(report, "ml_readiness", "label_suitability") or {}
        missing_metric = metric_by_name(report, "analytical_readiness", "missingness_structure") or {}
        missing_details = missing_metric.get("details") or {}

        g1 = gates.get("G1", {})
        g2 = gates.get("G2", {})
        g3 = gates.get("G3", {})
        g4 = gates.get("G4", {})

        n_bio = int(as_num(min_sample_details.get("n_biological_samples")) or as_num(g2.get("value")) or 0)
        n_groups = int(as_num(g3.get("value")) or 0)
        min_class_n = int(as_num(g4.get("value")) or 0)
        median_missing = as_num(missing_details.get("global_median_sample_missingness_rate"))

        base_sections = {
            key: section_score(report, key)
            for key in CORE_SECTION_KEYS
        }
        base_core = sum(base_sections.values()) / len(CORE_SECTION_KEYS)

        records.append(
            {
                "study_id": study_id,
                "state_path": payload.get("state_path", ""),
                "current_band": rs.get("final_band") or payload.get("band") or "No Data",
                "current_score": as_num(rs.get("core_ml_readiness_score")) or as_num(rs.get("score")) or 0.0,
                "computed_base_core_score": base_core,
                "current_provisional_band": rs.get("provisional_band") or band_from_score(base_core),
                "current_gate_ceiling": rs.get("gate_ceiling") or "",
                "g1_status": str(g1.get("status", "fail")),
                "g3_status": str(g3.get("status", "fail")),
                "n_biological_samples": n_bio,
                "n_groups": n_groups,
                "min_class_n": min_class_n,
                "median_sample_missingness_rate": median_missing,
                "minimum_sample_count_score_current": float(as_num(min_sample_metric.get("score")) or 0.0),
                "label_suitability_score_current": float(as_num(label_metric.get("score")) or 0.0),
                "base_structural": base_sections["structural"],
                "base_analytical": base_sections["analytical"],
                "base_annotation": base_sections["annotation"],
                "base_cohort": base_sections["cohort"],
                "base_ml_feasibility": base_sections["ml_feasibility"],
                "source": state.get("primary_source") or state.get("source") or "",
            }
        )
    return records


def evaluate_scenario(
    record: dict[str, Any],
    sample_threshold: int,
    min_class_threshold: int,
    missing_pass: float = 0.50,
    missing_fail: float = 0.80,
) -> dict[str, Any]:
    g1_status = record["g1_status"]
    g2_status = gate_status_for_sample_count(int(record["n_biological_samples"]), sample_threshold)
    g3_status = record["g3_status"]
    g4_status = gate_status_for_min_class(
        int(record["min_class_n"]),
        int(record["n_groups"]),
        min_class_threshold,
    )
    g5_status = gate_status_for_missingness(
        as_num(record.get("median_sample_missingness_rate")),
        missing_pass,
        missing_fail,
    )
    statuses = {"G1": g1_status, "G2": g2_status, "G3": g3_status, "G4": g4_status, "G5": g5_status}

    if g1_status == "fail":
        core_score = 0.0
        provisional_band = "Not Ready"
    else:
        sample_score = min(1.0, int(record["n_biological_samples"]) / sample_threshold)
        if int(record["n_groups"]) < 2:
            label_score = 0.0
        else:
            label_score = min(1.0, int(record["min_class_n"]) / min_class_threshold)
        structural = section_score_from_base(
            record["base_structural"],
            record["minimum_sample_count_score_current"],
            sample_score,
            fixed_count=5,
        )
        ml_feasibility = section_score_from_base(
            record["base_ml_feasibility"],
            record["label_suitability_score_current"],
            label_score,
            fixed_count=4,
        )
        core_score = (
            structural
            + record["base_analytical"]
            + record["base_annotation"]
            + record["base_cohort"]
            + ml_feasibility
        ) / 5
        provisional_band = band_from_score(core_score)

    ceiling = gate_ceiling(statuses)
    if ceiling == "No Data":
        final_band = "No Data"
    else:
        final_band = cap_band(provisional_band, ceiling)
    return {
        "g1_status": g1_status,
        "g2_status": g2_status,
        "g3_status": g3_status,
        "g4_status": g4_status,
        "g5_status": g5_status,
        "gate_ceiling": ceiling or "",
        "scenario_core_score": core_score,
        "scenario_provisional_band": provisional_band,
        "scenario_final_band": final_band,
    }


def section_score_from_base(base_score: float, old_metric_score: float, new_metric_score: float, fixed_count: int) -> float:
    return max(0.0, min(1.0, base_score + (new_metric_score - old_metric_score) / fixed_count))


def write_tsv_safe(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if not rows:
        path.write_text("")
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def scenario_id(sample_threshold: int, min_class_threshold: int) -> str:
    return f"sample{sample_threshold}_class{min_class_threshold}_missing50_80"


def scenario_label(sample_threshold: int, min_class_threshold: int) -> str:
    return f"N>={sample_threshold}\nclass>={min_class_threshold}"


def summarize_scenarios(per_study: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for row in per_study:
        by_scenario.setdefault(row["scenario_id"], []).append(row)

    summaries = []
    transitions = []
    for sid, rows in sorted(by_scenario.items(), key=lambda item: (int(item[1][0]["sample_threshold"]), int(item[1][0]["min_class_threshold"]))):
        n_total = len(rows)
        out = {
            "scenario_id": sid,
            "sample_threshold": rows[0]["sample_threshold"],
            "min_class_threshold": rows[0]["min_class_threshold"],
            "missingness_pass_threshold": "0.50",
            "missingness_fail_threshold": "0.80",
            "n_total": n_total,
            "changed_n": sum(row["changed_from_current"] == "1" for row in rows),
            "improved_n": sum(int(row["band_delta_rank"]) > 0 for row in rows),
            "worsened_n": sum(int(row["band_delta_rank"]) < 0 for row in rows),
        }
        out["unchanged_n"] = n_total - int(out["changed_n"])
        out["changed_pct"] = f"{100 * int(out['changed_n']) / n_total:.3f}"
        out["improved_pct"] = f"{100 * int(out['improved_n']) / n_total:.3f}"
        out["worsened_pct"] = f"{100 * int(out['worsened_n']) / n_total:.3f}"
        for band in BAND_ORDER:
            count = sum(row["scenario_final_band"] == band for row in rows)
            out[f"{band.lower().replace(' ', '_')}_n"] = count
            out[f"{band.lower().replace(' ', '_')}_pct"] = f"{100 * count / n_total:.3f}"
        for gate in ["G1", "G2", "G3", "G4", "G5"]:
            for status in ["pass", "warn", "fail"]:
                out[f"{gate}_{status}_n"] = sum(row[f"{gate.lower()}_status"] == status for row in rows)
        summaries.append(out)

        for current in BAND_ORDER:
            for scenario in BAND_ORDER:
                count = sum(row["current_band"] == current and row["scenario_final_band"] == scenario for row in rows)
                transitions.append(
                    {
                        "scenario_id": sid,
                        "sample_threshold": rows[0]["sample_threshold"],
                        "min_class_threshold": rows[0]["min_class_threshold"],
                        "current_band": current,
                        "scenario_final_band": scenario,
                        "n": count,
                    }
                )
    return summaries, transitions


def setup_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.linewidth": 0.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def save_figure(fig: plt.Figure, out_dir: Path, stem: str) -> None:
    for ext in ["pdf", "svg"]:
        fig.savefig(out_dir / f"{stem}.{ext}", bbox_inches="tight")
    fig.savefig(out_dir / f"{stem}.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def plot_band_composition(summary_rows: list[dict[str, Any]], out_dir: Path) -> None:
    rows = sorted(summary_rows, key=lambda row: (int(row["sample_threshold"]), int(row["min_class_threshold"])))
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(10.8, 4.9))
    bottoms = np.zeros(len(rows))
    for band in BAND_ORDER:
        vals = np.array([float(row[f"{band.lower().replace(' ', '_')}_pct"]) for row in rows])
        counts = [int(row[f"{band.lower().replace(' ', '_')}_n"]) for row in rows]
        ax.bar(x, vals, bottom=bottoms, color=BAND_COLORS[band], edgecolor="white", linewidth=0.75, label=band)
        for xi, bottom, val, count in zip(x, bottoms, vals, counts):
            if val >= 8:
                ax.text(
                    xi,
                    bottom + val / 2,
                    f"{count:,}\n{val:.0f}%",
                    ha="center",
                    va="center",
                    fontsize=6.6,
                    fontweight="bold",
                    color="white" if band in {"No Data", "Not Ready"} else TEXT_DARK,
                )
        bottoms += vals

    default_idx = next(i for i, row in enumerate(rows) if int(row["sample_threshold"]) == 20 and int(row["min_class_threshold"]) == 5)
    ax.add_patch(Rectangle((default_idx - 0.48, 0), 0.96, 100, fill=False, edgecolor=TEXT_DARK, linewidth=1.4))
    ax.text(default_idx, 104.5, "MERIT\nv7 default", ha="center", va="bottom", fontsize=7.4, fontweight="bold", color=TEXT_DARK)
    ax.set_ylim(0, 112)
    ax.set_xticks(x)
    ax.set_xticklabels([scenario_label(int(row["sample_threshold"]), int(row["min_class_threshold"])) for row in rows], fontweight="bold")
    ax.set_ylabel("Studies (%)", fontsize=10.2, fontweight="bold", color=TEXT_DARK)
    ax.set_title("Readiness band composition under alternate sample/class thresholds", loc="left", fontsize=13.2, fontweight="bold", color=TEXT_DARK)
    ax.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.85)
    ax.set_axisbelow(True)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.tick_params(axis="both", colors=TEXT_DARK)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=5, frameon=False, prop={"weight": "bold", "size": 8.2})
    fig.text(
        0.015,
        0.01,
        "Sensitivity varies the directly threshold-dependent scores and gates; missingness gate held at <=50% pass and >80% fail.",
        fontsize=8.4,
        fontweight="bold",
        color=TEXT_MUTED,
    )
    fig.subplots_adjust(left=0.08, right=0.99, top=0.86, bottom=0.27)
    save_figure(fig, out_dir, "figure5_threshold_sensitivity_band_composition")


def plot_change_heatmap(summary_rows: list[dict[str, Any]], out_dir: Path) -> None:
    sample_thresholds = [10, 20, 30]
    class_thresholds = [3, 5, 10]
    value = np.zeros((len(class_thresholds), len(sample_thresholds)))
    count = np.zeros_like(value, dtype=int)
    lookup = {(int(row["sample_threshold"]), int(row["min_class_threshold"])): row for row in summary_rows}
    for i, cls in enumerate(class_thresholds):
        for j, sample in enumerate(sample_thresholds):
            row = lookup[(sample, cls)]
            value[i, j] = float(row["changed_pct"])
            count[i, j] = int(row["changed_n"])

    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    im = ax.imshow(value, cmap="YlOrBr", vmin=0, vmax=max(1.0, float(np.max(value))))
    for i, cls in enumerate(class_thresholds):
        for j, sample in enumerate(sample_thresholds):
            ax.text(j, i, f"{value[i, j]:.1f}%\n(n={count[i, j]:,})", ha="center", va="center", fontsize=9, fontweight="bold", color=TEXT_DARK)
    ax.add_patch(Rectangle((1 - 0.5, 1 - 0.5), 1, 1, fill=False, edgecolor="#0b1f26", linewidth=1.8))
    ax.set_xticks(np.arange(len(sample_thresholds)))
    ax.set_yticks(np.arange(len(class_thresholds)))
    ax.set_xticklabels([str(x) for x in sample_thresholds], fontweight="bold")
    ax.set_yticklabels([str(x) for x in class_thresholds], fontweight="bold")
    ax.set_xlabel("Biological sample threshold for G2", fontsize=10.2, fontweight="bold", color=TEXT_DARK)
    ax.set_ylabel("Minimum class threshold for G4", fontsize=10.2, fontweight="bold", color=TEXT_DARK)
    ax.set_title("Studies whose final band changes vs MERIT v7 default", loc="left", fontsize=12.5, fontweight="bold", color=TEXT_DARK)
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Changed studies (%)", fontsize=9.2, fontweight="bold")
    ax.tick_params(axis="both", colors=TEXT_DARK)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(0.02, 0.02, "Outlined cell is the current default: sample >=20, smallest class >=5.", fontsize=8.2, fontweight="bold", color=TEXT_MUTED)
    fig.subplots_adjust(left=0.16, right=0.95, top=0.88, bottom=0.18)
    save_figure(fig, out_dir, "figure5_threshold_sensitivity_changed_heatmap")


def plot_transition_extremes(transitions: list[dict[str, Any]], out_dir: Path) -> None:
    scenarios = [
        ("sample10_class3_missing50_80", "Permissive\nN>=10, class>=3"),
        ("sample30_class10_missing50_80", "Strict\nN>=30, class>=10"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 4.8), sharey=True)
    vmax = 0
    matrices: list[np.ndarray] = []
    for sid, _ in scenarios:
        mat = np.zeros((len(BAND_ORDER), len(BAND_ORDER)), dtype=int)
        for row in transitions:
            if row["scenario_id"] != sid:
                continue
            i = BAND_ORDER.index(row["current_band"])
            j = BAND_ORDER.index(row["scenario_final_band"])
            mat[i, j] = int(row["n"])
        matrices.append(mat)
        vmax = max(vmax, int(mat.max()))

    for ax, (sid, label), mat in zip(axes, scenarios, matrices):
        im = ax.imshow(mat, cmap="GnBu", vmin=0, vmax=vmax)
        for i in range(len(BAND_ORDER)):
            for j in range(len(BAND_ORDER)):
                val = int(mat[i, j])
                if val:
                    ax.text(j, i, f"{val:,}", ha="center", va="center", fontsize=7.8, fontweight="bold", color=TEXT_DARK)
        ax.set_title(label, fontsize=11.5, fontweight="bold", color=TEXT_DARK)
        ax.set_xticks(np.arange(len(BAND_ORDER)))
        ax.set_xticklabels(BAND_ORDER, rotation=35, ha="right", fontweight="bold", fontsize=7.4)
        ax.set_yticks(np.arange(len(BAND_ORDER)))
        ax.set_yticklabels(BAND_ORDER, fontweight="bold", fontsize=8.0)
        ax.set_xlabel("Scenario final band", fontsize=8.8, fontweight="bold", color=TEXT_DARK)
        for spine in ax.spines.values():
            spine.set_visible(False)
    axes[0].set_ylabel("")
    fig.suptitle("Band transitions under permissive and strict threshold profiles", x=0.02, ha="left", fontsize=13.0, fontweight="bold", color=TEXT_DARK)
    fig.subplots_adjust(left=0.11, right=0.86, top=0.82, bottom=0.24, wspace=0.36)
    cax = fig.add_axes([0.90, 0.24, 0.018, 0.58])
    cbar = fig.colorbar(im, cax=cax)
    cbar.set_label("Studies (n)", fontsize=9.2, fontweight="bold")
    save_figure(fig, out_dir, "figure5_threshold_sensitivity_transition_extremes")


def write_readme(out_dir: Path, records: list[dict[str, Any]], summaries: list[dict[str, Any]], validation_mismatches: int) -> None:
    lookup = {(int(row["sample_threshold"]), int(row["min_class_threshold"])): row for row in summaries}
    default = lookup[(20, 5)]
    permissive = lookup[(10, 3)]
    strict = lookup[(30, 10)]
    max_changed = max(summaries, key=lambda row: float(row["changed_pct"]))
    text = f"""# MERIT v7 Threshold Sensitivity Analysis

Source cache: `merit-cache-workbench-full-v7`

Unit: one study-level workflow state from `index.json` (`n={len(records):,}` studies).

This is a post hoc sensitivity analysis. It does not recompute source parsing or metric extraction. It varies only the directly threshold-dependent components:

- `minimum_sample_count` score and G2 biological-sample gate: thresholds 10, 20, 30.
- `label_suitability` score and G4 minimum-class gate: thresholds 3, 5, 10.
- G5 sample-level missingness is held at the MERIT v7 default: pass <=50%, warn <=80%, fail >80%.

The default cell is sample threshold 20 and minimum-class threshold 5.

Default-validation mismatches against the cached MERIT v7 final band: `{validation_mismatches}`.

## Key Results

- Default band counts: Ready {default['ready_n']:,}, Conditional {default['conditional_n']:,}, Fragile {default['fragile_n']:,}, Not Ready {default['not_ready_n']:,}, No Data {default['no_data_n']:,}.
- Most permissive profile (`N>=10`, class `>=3`) changes {int(permissive['changed_n']):,} studies ({float(permissive['changed_pct']):.1f}%): {int(permissive['improved_n']):,} improve and {int(permissive['worsened_n']):,} worsen.
- Strictest profile (`N>=30`, class `>=10`) changes {int(strict['changed_n']):,} studies ({float(strict['changed_pct']):.1f}%): {int(strict['improved_n']):,} improve and {int(strict['worsened_n']):,} worsen.
- Maximum change over the 3x3 sensitivity grid occurs for `{max_changed['scenario_id']}`: {int(max_changed['changed_n']):,} studies ({float(max_changed['changed_pct']):.1f}%).

## Files

- `threshold_sensitivity_per_study.tsv`: per-study band under each threshold profile.
- `threshold_sensitivity_scenario_summary.tsv`: band counts and changed-study counts per profile.
- `threshold_sensitivity_transition_summary.tsv`: current-band to scenario-band transition counts.
- `figure5_threshold_sensitivity_band_composition.*`: stacked band composition across scenarios.
- `figure5_threshold_sensitivity_changed_heatmap.*`: percent of studies whose final band changes.
- `figure5_threshold_sensitivity_transition_extremes.*`: transitions for permissive and strict profiles.
"""
    (out_dir / "README.md").write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", default="merit-cache-workbench-full-v7")
    parser.add_argument(
        "--out-dir",
        default="merit/manuscript/figures/Figure-5/latest/threshold_sensitivity_v7",
    )
    args = parser.parse_args()

    cache_root = Path(args.cache_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = load_records(cache_root)
    per_study = []
    for sample_threshold, min_class_threshold in product([10, 20, 30], [3, 5, 10]):
        sid = scenario_id(sample_threshold, min_class_threshold)
        for record in records:
            scenario = evaluate_scenario(record, sample_threshold, min_class_threshold)
            current_rank = BAND_RANK.get(record["current_band"], 0)
            scenario_rank = BAND_RANK.get(scenario["scenario_final_band"], 0)
            per_study.append(
                {
                    "study_id": record["study_id"],
                    "scenario_id": sid,
                    "sample_threshold": sample_threshold,
                    "sample_warn_floor": max(5, sample_threshold // 2),
                    "min_class_threshold": min_class_threshold,
                    "missingness_pass_threshold": "0.50",
                    "missingness_fail_threshold": "0.80",
                    "current_band": record["current_band"],
                    "current_score": f"{record['current_score']:.6f}",
                    "scenario_core_score": f"{scenario['scenario_core_score']:.6f}",
                    "scenario_provisional_band": scenario["scenario_provisional_band"],
                    "scenario_final_band": scenario["scenario_final_band"],
                    "changed_from_current": "1" if scenario["scenario_final_band"] != record["current_band"] else "0",
                    "band_delta_rank": scenario_rank - current_rank,
                    "gate_ceiling": scenario["gate_ceiling"],
                    "g1_status": scenario["g1_status"],
                    "g2_status": scenario["g2_status"],
                    "g3_status": scenario["g3_status"],
                    "g4_status": scenario["g4_status"],
                    "g5_status": scenario["g5_status"],
                    "n_biological_samples": record["n_biological_samples"],
                    "n_groups": record["n_groups"],
                    "min_class_n": record["min_class_n"],
                    "median_sample_missingness_rate": "" if record["median_sample_missingness_rate"] is None else f"{record['median_sample_missingness_rate']:.6f}",
                    "state_path": record["state_path"],
                }
            )

    summaries, transitions = summarize_scenarios(per_study)
    validation_mismatches = sum(
        row["scenario_id"] == "sample20_class5_missing50_80" and row["changed_from_current"] == "1"
        for row in per_study
    )

    per_study_fields = [
        "study_id", "scenario_id", "sample_threshold", "sample_warn_floor", "min_class_threshold",
        "missingness_pass_threshold", "missingness_fail_threshold", "current_band", "current_score",
        "scenario_core_score", "scenario_provisional_band", "scenario_final_band", "changed_from_current",
        "band_delta_rank", "gate_ceiling", "g1_status", "g2_status", "g3_status", "g4_status", "g5_status",
        "n_biological_samples", "n_groups", "min_class_n", "median_sample_missingness_rate", "state_path",
    ]
    write_tsv_safe(out_dir / "threshold_sensitivity_per_study.tsv", per_study, per_study_fields)
    write_tsv_safe(out_dir / "threshold_sensitivity_scenario_summary.tsv", summaries)
    write_tsv_safe(out_dir / "threshold_sensitivity_transition_summary.tsv", transitions)
    write_tsv_safe(out_dir / "threshold_sensitivity_default_validation.tsv", [
        {
            "default_scenario_id": "sample20_class5_missing50_80",
            "n_records": len(records),
            "mismatches_vs_cached_current_band": validation_mismatches,
        }
    ])

    setup_style()
    plot_band_composition(summaries, out_dir)
    plot_change_heatmap(summaries, out_dir)
    plot_transition_extremes(transitions, out_dir)
    write_readme(out_dir, records, summaries, validation_mismatches)

    print(f"Wrote threshold sensitivity analysis to {out_dir}")
    print(f"Default validation mismatches: {validation_mismatches}")
    for row in summaries:
        if row["scenario_id"] in {"sample10_class3_missing50_80", "sample20_class5_missing50_80", "sample30_class10_missing50_80"}:
            print(
                row["scenario_id"],
                "changed",
                row["changed_n"],
                f"({float(row['changed_pct']):.1f}%)",
                "Ready",
                row["ready_n"],
                "Conditional",
                row["conditional_n"],
                "Fragile",
                row["fragile_n"],
                "Not Ready",
                row["not_ready_n"],
                "No Data",
                row["no_data_n"],
            )


if __name__ == "__main__":
    main()
