#!/usr/bin/env python3
"""Generate ReadinessScore distribution analyses for manuscript reporting.

Outputs are written under:
  merit/manuscript/supplementary/readinessscore_distribution/

Data source:
  merit-cache-workbench-full-v6/json/*_workflow_state.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np


BAND_ORDER = ["Ready", "Conditional", "Fragile", "Not Ready", "No Data"]
SOURCE_ORDER = ["datatable", "mwtab", "untarg_data"]
DIMENSIONS = ["structural", "metadata", "analytical", "annotation", "cohort", "ml_feasibility"]


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _readiness_confidence(summary: dict[str, Any], section_scores: dict[str, float]) -> str:
    """Reproduce UI confidence logic from merit/ui.py."""
    n_matrices = summary.get("n_feature_matrices") or 0
    n_bio = summary.get("n_biological_samples") or 0

    if n_matrices == 0:
        return "Low"
    if n_bio < 10:
        return "Low"

    n_informative = 0
    for key in ("structural", "metadata", "analytical", "annotation", "cohort", "ml_feasibility"):
        if key == "ml_feasibility":
            s = section_scores.get("ml_feasibility", section_scores.get("ml", 0))
        else:
            s = section_scores.get(key, 0)
        if s > 0.55 or s < 0.45:
            n_informative += 1

    reasons = 0
    if n_informative <= 2:
        reasons += 1
    if n_bio < 30:
        reasons += 1
    meta_score = section_scores.get("metadata", 0)
    analytical_score = section_scores.get("analytical", 0)
    if meta_score < 0.5:
        reasons += 1
    if analytical_score < 0.5:
        reasons += 1

    if reasons >= 2:
        return "Low"
    if n_informative >= 5 and n_bio >= 50 and meta_score >= 0.65:
        return "High"
    return "Moderate"


def _extract_analysis_ids(bundle: dict[str, Any]) -> list[str]:
    ids: set[str] = set()

    for item in bundle.get("tabular_resolution") or []:
        if isinstance(item, dict):
            aid = str(item.get("analysis_id", "")).strip()
            if aid:
                ids.add(aid)

    analysis_json_paths = bundle.get("analysis_json_paths")
    if isinstance(analysis_json_paths, dict):
        for aid in analysis_json_paths.keys():
            aid = str(aid).strip()
            if aid:
                ids.add(aid)

    for p in bundle.get("file_manifest") or []:
        if not isinstance(p, str):
            continue
        m = re.search(r"/(AN\d{6})/", p)
        if m:
            ids.add(m.group(1))

    return sorted(ids)


def _band_from_readiness(rs: dict[str, Any]) -> str:
    band = rs.get("final_band") or rs.get("band") or "No Data"
    return str(band)


def _dim_scores(rs: dict[str, Any]) -> dict[str, float | None]:
    sec = rs.get("section_scores") or {}
    out: dict[str, float | None] = {}
    for d in DIMENSIONS:
        if d == "ml_feasibility":
            out[d] = _safe_float(sec.get("ml_feasibility", sec.get("ml")))
        else:
            out[d] = _safe_float(sec.get(d))
    return out


def _platform_norm(v: Any) -> str:
    s = str(v or "").strip()
    return s if s else "Unknown"


@dataclass
class BuildResult:
    df: pd.DataFrame
    n_workflow_files: int


def build_analysis_table(cache_root: Path) -> BuildResult:
    json_dir = cache_root / "json"
    paths = sorted(json_dir.glob("*_workflow_state.json"))
    rows: list[dict[str, Any]] = []

    for p in paths:
        with p.open() as f:
            w = json.load(f)

        study_id = str(w.get("study_id", "")).upper()
        bundle = w.get("bundle") or {}
        source_assessments = w.get("source_assessments") or {}
        study_level_rs = w.get("readiness_score") or {}
        study_level_band = _band_from_readiness(study_level_rs)
        study_level_dims = _dim_scores(study_level_rs)
        study_level_summary = (w.get("final_report") or {}).get("ingestion_summary") or {}
        study_level_conf = _readiness_confidence(study_level_summary, study_level_rs.get("section_scores") or {})

        emitted = 0
        for source in SOURCE_ORDER:
            sa = source_assessments.get(source)
            if not isinstance(sa, dict):
                continue
            rs = sa.get("readiness_score") or {}
            report = sa.get("report") or {}
            summary = report.get("ingestion_summary") or {}
            per_analysis = summary.get("per_analysis") or []
            band = _band_from_readiness(rs)
            dims = _dim_scores(rs)
            conf = _readiness_confidence(summary, rs.get("section_scores") or {})
            score = _safe_float(rs.get("score"))

            for a in per_analysis:
                if not isinstance(a, dict):
                    continue
                n_features = _safe_float(a.get("n_features")) or 0.0
                if n_features <= 0:
                    continue
                emitted += 1
                rows.append(
                    {
                        "study_id": study_id,
                        "analysis_id": str(a.get("analysis_id", "")).strip(),
                        "source": source,
                        "platform": str(a.get("platform", "") or "").strip(),
                        "platform_norm": _platform_norm(a.get("platform")),
                        "analysis_type": str(a.get("analysis_type", "") or "").strip(),
                        "n_samples": _safe_float(a.get("n_samples")),
                        "n_features": _safe_float(a.get("n_features")),
                        "score": score,
                        "band": band,
                        "confidence": conf,
                        **dims,
                    }
                )

        if emitted == 0:
            analysis_ids = _extract_analysis_ids(bundle)
            if not analysis_ids:
                analysis_ids = ["(none)"]
            for aid in analysis_ids:
                rows.append(
                    {
                        "study_id": study_id,
                        "analysis_id": aid,
                        "source": "none",
                        "platform": "",
                        "platform_norm": "Unknown",
                        "analysis_type": "",
                        "n_samples": _safe_float(study_level_summary.get("n_samples")),
                        "n_features": _safe_float(study_level_summary.get("n_features")),
                        "score": _safe_float(study_level_rs.get("score")),
                        "band": study_level_band,
                        "confidence": study_level_conf,
                        **study_level_dims,
                    }
                )

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"No analysis rows built from {json_dir}")
    return BuildResult(df=df, n_workflow_files=len(paths))


def _band_distribution(df: pd.DataFrame) -> pd.DataFrame:
    vc = df["band"].value_counts(dropna=False)
    total = int(vc.sum())
    out = []
    for b in BAND_ORDER:
        c = int(vc.get(b, 0))
        out.append({"band": b, "count": c, "percent": (100.0 * c / total) if total else 0.0})
    return pd.DataFrame(out)


def _source_score_summary(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["source"].isin(SOURCE_ORDER)].copy()
    g = d.groupby("source", dropna=False)
    out = g.agg(
        n_rows=("analysis_id", "size"),
        n_studies=("study_id", pd.Series.nunique),
        mean_score=("score", "mean"),
        median_score=("score", "median"),
        std_score=("score", "std"),
    ).reset_index()
    out["source"] = pd.Categorical(out["source"], SOURCE_ORDER, ordered=True)
    out = out.sort_values("source").reset_index(drop=True)
    return out


def _source_band_mix(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["source"].isin(SOURCE_ORDER)].copy()
    ct = pd.crosstab(d["source"], d["band"])
    for b in BAND_ORDER:
        if b not in ct.columns:
            ct[b] = 0
    ct = ct[BAND_ORDER]
    ct["total"] = ct.sum(axis=1)
    for b in BAND_ORDER:
        ct[f"{b}_pct"] = (100.0 * ct[b] / ct["total"]).fillna(0.0)
    ct = ct.reset_index()
    ct["source"] = pd.Categorical(ct["source"], SOURCE_ORDER, ordered=True)
    ct = ct.sort_values("source").reset_index(drop=True)
    return ct


def _source_dimension_summary(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["source"].isin(SOURCE_ORDER)].copy()
    out = d.groupby("source", dropna=False)[DIMENSIONS].mean().reset_index()
    out["source"] = pd.Categorical(out["source"], SOURCE_ORDER, ordered=True)
    out = out.sort_values("source").reset_index(drop=True)
    return out


def _source_dimension_deltas(source_dim: pd.DataFrame) -> pd.DataFrame:
    long = source_dim.melt(id_vars=["source"], value_vars=DIMENSIONS, var_name="dimension", value_name="mean_score")
    out = []
    for dim, sub in long.groupby("dimension"):
        s = sub.set_index("source")["mean_score"]
        vmax = float(s.max())
        vmin = float(s.min())
        out.append(
            {
                "dimension": dim,
                "max_source": s.idxmax(),
                "max_mean_score": vmax,
                "min_source": s.idxmin(),
                "min_mean_score": vmin,
                "spread_max_minus_min": vmax - vmin,
                "datatable_minus_mwtab": _safe_float(s.get("datatable")) - _safe_float(s.get("mwtab")) if ("datatable" in s.index and "mwtab" in s.index) else None,
                "datatable_minus_untarg_data": _safe_float(s.get("datatable")) - _safe_float(s.get("untarg_data")) if ("datatable" in s.index and "untarg_data" in s.index) else None,
                "mwtab_minus_untarg_data": _safe_float(s.get("mwtab")) - _safe_float(s.get("untarg_data")) if ("mwtab" in s.index and "untarg_data" in s.index) else None,
            }
        )
    return pd.DataFrame(out).sort_values("spread_max_minus_min", ascending=False).reset_index(drop=True)


def _study_spread(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("study_id", dropna=False)
    out = g.agg(
        n_analysis_rows=("analysis_id", "size"),
        n_unique_analyses=("analysis_id", pd.Series.nunique),
        n_sources=("source", pd.Series.nunique),
        n_platforms=("platform_norm", pd.Series.nunique),
        score_mean=("score", "mean"),
        score_min=("score", "min"),
        score_max=("score", "max"),
        score_std=("score", "std"),
    ).reset_index()
    out["score_range"] = out["score_max"] - out["score_min"]
    band_sets = g["band"].apply(lambda x: "|".join(sorted(set(map(str, x)))))
    out = out.merge(band_sets.rename("band_set"), on="study_id", how="left")
    out["n_distinct_bands"] = out["band_set"].map(lambda s: len(set(s.split("|"))) if s else 0)
    return out.sort_values(["n_unique_analyses", "score_range"], ascending=[False, False]).reset_index(drop=True)


def _cramers_v_from_crosstab(ct: pd.DataFrame) -> float:
    observed = ct.to_numpy(dtype=float)
    n = observed.sum()
    if n <= 1:
        return float("nan")
    row_sum = observed.sum(axis=1, keepdims=True)
    col_sum = observed.sum(axis=0, keepdims=True)
    expected = row_sum @ col_sum / n
    mask = expected > 0
    chi2_terms = np.zeros_like(observed, dtype=float)
    chi2_terms[mask] = ((observed[mask] - expected[mask]) ** 2) / expected[mask]
    chi2 = chi2_terms.sum()
    r, k = observed.shape
    if r <= 1 or k <= 1:
        return float("nan")
    phi2 = chi2 / n
    phi2corr = max(0.0, phi2 - ((k - 1) * (r - 1)) / (n - 1))
    rcorr = r - ((r - 1) ** 2) / (n - 1)
    kcorr = k - ((k - 1) ** 2) / (n - 1)
    denom = min(kcorr - 1, rcorr - 1)
    if denom <= 0:
        return 0.0
    return math.sqrt(phi2corr / denom)


def _df_to_markdown(df: pd.DataFrame, floatfmt: str = ".4f") -> str:
    cols = [str(c) for c in df.columns]

    def _fmt(v: Any) -> str:
        if isinstance(v, (float, np.floating)):
            if math.isnan(float(v)):
                return ""
            return format(float(v), floatfmt)
        return str(v)

    data_rows = [[_fmt(v) for v in row] for row in df.to_numpy(dtype=object)]
    widths = [len(c) for c in cols]
    for row in data_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def _line(cells: list[str]) -> str:
        padded = [cells[i].ljust(widths[i]) for i in range(len(cells))]
        return "| " + " | ".join(padded) + " |"

    header = _line(cols)
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    body = [_line(r) for r in data_rows]
    return "\n".join([header, sep, *body])


def _platform_band_outputs(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = df.copy()
    d["platform_norm"] = d["platform_norm"].fillna("Unknown")
    counts = d["platform_norm"].value_counts()
    keep = set(counts[counts >= 20].index.tolist())
    d["platform_bucket"] = d["platform_norm"].apply(lambda x: x if x in keep else "Other (<20)")
    ct = pd.crosstab(d["platform_bucket"], d["band"])
    for b in BAND_ORDER:
        if b not in ct.columns:
            ct[b] = 0
    ct = ct[BAND_ORDER]
    ct_counts = ct.copy()
    ct_counts["total"] = ct_counts.sum(axis=1)
    ct_counts = ct_counts.sort_values("total", ascending=False)
    ctab_reset = ct_counts.reset_index()

    assoc = pd.DataFrame(
        [
            {
                "n_rows_used": int(ct_counts["total"].sum()),
                "n_platform_buckets": int(ct.shape[0]),
                "n_bands": int(ct.shape[1]),
                "cramers_v": _cramers_v_from_crosstab(ct),
            }
        ]
    )
    return ctab_reset, assoc


def _dimension_means_and_bottleneck(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    means = []
    for d in DIMENSIONS:
        means.append({"dimension": d, "mean_score": float(df[d].mean())})
    dim_means = pd.DataFrame(means).sort_values("mean_score", ascending=True).reset_index(drop=True)
    global_min_dim = str(dim_means.iloc[0]["dimension"])
    global_min_mean = float(dim_means.iloc[0]["mean_score"])

    tie_counts = {d: 0.0 for d in DIMENSIONS}
    for _, row in df[DIMENSIONS].iterrows():
        vals = {d: row[d] for d in DIMENSIONS if pd.notna(row[d])}
        if not vals:
            continue
        m = min(vals.values())
        mins = [d for d, v in vals.items() if abs(v - m) < 1e-12]
        w = 1.0 / len(mins)
        for d in mins:
            tie_counts[d] += w

    bottleneck_rows = []
    n = len(df)
    for d in DIMENSIONS:
        bottleneck_rows.append(
            {
                "dimension": d,
                "mean_score": float(df[d].mean()),
                "weighted_min_count": tie_counts[d],
                "weighted_min_percent": (100.0 * tie_counts[d] / n) if n else 0.0,
                "is_global_lowest_mean": d == global_min_dim,
            }
        )
    bottleneck = pd.DataFrame(bottleneck_rows).sort_values("mean_score", ascending=True).reset_index(drop=True)
    summary = pd.DataFrame(
        [
            {
                "global_bottleneck_dimension": global_min_dim,
                "global_bottleneck_mean_score": global_min_mean,
            }
        ]
    )
    return dim_means, pd.concat([summary, bottleneck], ignore_index=True)


def _confidence_distribution(df: pd.DataFrame) -> pd.DataFrame:
    order = ["Low", "Moderate", "High"]
    vc = df["confidence"].value_counts(dropna=False)
    total = int(vc.sum())
    out = []
    for c in order:
        n = int(vc.get(c, 0))
        out.append({"confidence": c, "count": n, "percent": (100.0 * n / total) if total else 0.0})
    return pd.DataFrame(out)


def _write_markdown(
    out_path: Path,
    df: pd.DataFrame,
    n_workflow_files: int,
    band_dist: pd.DataFrame,
    source_score: pd.DataFrame,
    source_dim_deltas: pd.DataFrame,
    study_spread: pd.DataFrame,
    platform_assoc: pd.DataFrame,
    bottleneck: pd.DataFrame,
    conf_dist: pd.DataFrame,
) -> None:
    total_rows = len(df)
    total_studies = df["study_id"].nunique()
    multi_study = int((study_spread["n_unique_analyses"] > 1).sum())
    top_spread = study_spread.sort_values("score_range", ascending=False).head(5)
    bottleneck_dim = str(bottleneck.iloc[0]["global_bottleneck_dimension"]) if not bottleneck.empty else "NA"
    bottleneck_score = float(bottleneck.iloc[0]["global_bottleneck_mean_score"]) if not bottleneck.empty else float("nan")

    lines = []
    lines.append("# ReadinessScore Distribution Across the Repository\n")
    lines.append(f"- Workflow files parsed: **{n_workflow_files}**")
    lines.append(f"- Analysis rows: **{total_rows}**")
    lines.append(f"- Unique studies represented: **{total_studies}**\n")

    lines.append("## 1) Overall Readiness Band Distribution\n")
    lines.append(_df_to_markdown(band_dist, floatfmt=".2f"))
    lines.append("")

    lines.append("## 2) Source-Stratified Breakdown\n")
    lines.append("### Score Summary by Source\n")
    lines.append(_df_to_markdown(source_score, floatfmt=".4f"))
    lines.append("\n### Dimensions Driving Source Differences (ranked by spread)\n")
    lines.append(_df_to_markdown(source_dim_deltas.head(6), floatfmt=".4f"))
    lines.append("")

    lines.append("## 3) Study-Level Aggregation and Spread\n")
    lines.append(f"- Studies with >1 analysis: **{multi_study}**")
    lines.append("\n### Top 5 Studies by Within-Study Score Range\n")
    cols = ["study_id", "n_unique_analyses", "n_sources", "n_platforms", "score_min", "score_max", "score_range", "n_distinct_bands"]
    lines.append(_df_to_markdown(top_spread[cols], floatfmt=".4f"))
    lines.append("\n### Platform vs Band Association\n")
    lines.append(_df_to_markdown(platform_assoc, floatfmt=".4f"))
    lines.append("")

    lines.append("## 4) Dimension-Wise Bottleneck\n")
    lines.append(
        f"- Universal lowest-scoring dimension (mean): **{bottleneck_dim}** "
        f"(mean score **{bottleneck_score:.4f}**)\n"
    )
    lines.append("## 5) Score Confidence Distribution\n")
    lines.append(_df_to_markdown(conf_dist, floatfmt=".2f"))
    lines.append("")

    out_path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-root",
        default="/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v6",
        help="Path to merit-cache-workbench-full-v6 directory.",
    )
    parser.add_argument(
        "--out-dir",
        default="/home/shayantan/metabolomics/ML-ready/merit/manuscript/supplementary/readinessscore_distribution",
        help="Output directory for TSV + markdown outputs.",
    )
    args = parser.parse_args()

    cache_root = Path(args.cache_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = build_analysis_table(cache_root)
    df = result.df.copy()

    # Canonical ordering and typing
    df["band"] = df["band"].astype(str)
    df["confidence"] = df["confidence"].astype(str)
    for c in ["n_samples", "n_features", "score", *DIMENSIONS]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    band_dist = _band_distribution(df)
    source_score = _source_score_summary(df)
    source_band_mix = _source_band_mix(df)
    source_dim = _source_dimension_summary(df)
    source_dim_deltas = _source_dimension_deltas(source_dim)
    study_spread = _study_spread(df)
    platform_band_ct, platform_assoc = _platform_band_outputs(df)
    dim_means, bottleneck = _dimension_means_and_bottleneck(df)
    conf_dist = _confidence_distribution(df)

    df.to_csv(out_dir / "readiness_analysis_long.tsv", sep="\t", index=False)
    band_dist.to_csv(out_dir / "overall_band_distribution.tsv", sep="\t", index=False)
    source_score.to_csv(out_dir / "source_score_summary.tsv", sep="\t", index=False)
    source_band_mix.to_csv(out_dir / "source_band_mix.tsv", sep="\t", index=False)
    source_dim.to_csv(out_dir / "source_dimension_summary.tsv", sep="\t", index=False)
    source_dim_deltas.to_csv(out_dir / "source_dimension_deltas.tsv", sep="\t", index=False)
    study_spread.to_csv(out_dir / "study_level_spread.tsv", sep="\t", index=False)
    platform_band_ct.to_csv(out_dir / "platform_band_crosstab.tsv", sep="\t", index=False)
    platform_assoc.to_csv(out_dir / "platform_band_association.tsv", sep="\t", index=False)
    dim_means.to_csv(out_dir / "dimension_mean_scores.tsv", sep="\t", index=False)
    bottleneck.to_csv(out_dir / "dimension_bottleneck_summary.tsv", sep="\t", index=False)
    conf_dist.to_csv(out_dir / "score_confidence_distribution.tsv", sep="\t", index=False)

    _write_markdown(
        out_path=out_dir / "readinessscore_distribution_report.md",
        df=df,
        n_workflow_files=result.n_workflow_files,
        band_dist=band_dist,
        source_score=source_score,
        source_dim_deltas=source_dim_deltas,
        study_spread=study_spread,
        platform_assoc=platform_assoc,
        bottleneck=bottleneck,
        conf_dist=conf_dist,
    )

    print(f"Wrote outputs to: {out_dir}")
    print(f"Analysis rows: {len(df)}; studies: {df['study_id'].nunique()}; workflow files: {result.n_workflow_files}")


if __name__ == "__main__":
    main()
