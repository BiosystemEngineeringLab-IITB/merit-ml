#!/usr/bin/python3
"""Measurement scale: distribution-dependent summary of per-analysis median positive values. 18 Sep 2026 frozen cache.

Input: outputs/table4_descriptors_20260927/table4_per_analysis.tsv (per valid matrix: median_positive, p05_positive,
p95_positive, n_negative). Analyses with any negative value (already centred/scaled; defined by content, not by
location) or without positive values are reported separately and left out of the model.
Variable: x = log10(median of positive values) per analysis.
Groups: Tier 1 pooled (datatable + mwTab), datatable, mwTab, untarg_data.
Model: Gaussian mixtures with k = 1, 2, 3 (scikit-learn, full covariance, n_init = 10, random_state = 0); BIC decides.
For k = 2: component means / SDs / weights, the low-scale weight (share of analyses in the low-mean component) and the
decision boundary (x between the means where both weighted component densities are equal).
Uncertainty: 1,000 bootstrap replicates resampling STUDIES with replacement (all analyses of a drawn study enter), seed
20260930; 95% percentile intervals. Sensitivity: minimum of a Gaussian KDE (scipy, Scott bandwidth) between the modes,
with the same bootstrap.
Also reported: dynamic range = log10(p95 / p5) of positive values per analysis (used in the figure).
Outputs (this folder): scale_per_analysis.tsv, scale_mixture_summary.tsv, scale_mixture_bic.tsv, scale_mixture_summary.txt
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde, norm
from sklearn.mixture import GaussianMixture

SRC = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/outputs/table4_descriptors_20260927/table4_per_analysis.tsv")
OUT = Path(__file__).resolve().parent
B = 1000
SEED = 20260930
GRID = np.linspace(-4, 10, 2801)


def fit(x: np.ndarray, k: int) -> GaussianMixture:
    return GaussianMixture(n_components=k, covariance_type="full", n_init=10, random_state=0).fit(x.reshape(-1, 1))


def two_comp(x: np.ndarray) -> dict:
    g = fit(x, 2)
    m = g.means_.ravel()
    s = np.sqrt(g.covariances_.ravel())
    w = g.weights_
    lo, hi = np.argsort(m)
    between = GRID[(GRID > m[lo]) & (GRID < m[hi])]
    diff = w[lo] * norm.pdf(between, m[lo], s[lo]) - w[hi] * norm.pdf(between, m[hi], s[hi])
    cross = between[np.where(np.diff(np.sign(diff)) != 0)[0]]
    boundary = float(cross[0]) if len(cross) else np.nan
    dens = gaussian_kde(x)(between)
    dip = float(between[np.argmin(dens)]) if len(between) else np.nan
    return {"mean_low": m[lo], "sd_low": s[lo], "w_low": w[lo], "mean_high": m[hi], "sd_high": s[hi], "w_high": w[hi],
            "boundary": boundary, "kde_dip": dip, "share_below_boundary": float((x < boundary).mean())}


def main() -> None:
    d = pd.read_csv(SRC, sep="\t")
    for c in ("median_positive", "p05_positive", "p95_positive", "n_negative"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    assert {"p05_positive", "p95_positive"} <= set(d.columns), "run table4_descriptors.py with percentiles first"
    d["has_negative"] = d.n_negative > 0
    d["no_positive"] = d.median_positive.isna() | (d.median_positive <= 0)
    d["x"] = np.log10(d.median_positive.where(d.median_positive > 0))
    d["dynamic_range"] = np.log10(d.p95_positive / d.p05_positive)
    d["modelled"] = ~d.has_negative & ~d.no_positive
    d[["study_id", "analysis_id", "source", "median_positive", "x", "dynamic_range", "has_negative", "no_positive",
       "modelled"]].to_csv(OUT / "scale_per_analysis.tsv", sep="\t", index=False)
    groups = {"Tier 1 pooled": d.source.isin(["datatable", "mwtab"]), "datatable": d.source == "datatable",
              "mwtab": d.source == "mwtab", "untarg_data": d.source == "untarg_data"}
    rng = np.random.default_rng(SEED)
    bic_rows, rows, txt = [], [], []
    for gname, mask in groups.items():
        g = d[mask & d.modelled]
        x = g.x.to_numpy()
        for k in (1, 2, 3):
            bic_rows.append({"group": gname, "k": k, "n": len(x), "bic": fit(x, k).bic(x.reshape(-1, 1))})
        est = two_comp(x)
        studies = g.study_id.unique()
        idx = {s: np.where(g.study_id.to_numpy() == s)[0] for s in studies}
        boots = []
        for _ in range(B):
            pick = rng.choice(studies, size=len(studies), replace=True)
            xb = x[np.concatenate([idx[s] for s in pick])]
            boots.append(two_comp(xb))
        bt = pd.DataFrame(boots)
        row = {"group": gname, "analyses_modelled": len(x), "studies": len(studies),
               "excluded_negative": int((mask & d.has_negative).sum()), "excluded_no_positive": int((mask & d.no_positive).sum())}
        for k, v in est.items():
            row[k] = v
            row[f"{k}_ci_lo"] = bt[k].quantile(0.025)
            row[f"{k}_ci_hi"] = bt[k].quantile(0.975)
        rows.append(row)
        bics = {r["k"]: r["bic"] for r in bic_rows if r["group"] == gname}
        best = min(bics, key=bics.get)
        txt.append(f"{gname}: n={len(x):,} analyses ({len(studies):,} studies); BIC k1 {bics[1]:.0f}, k2 {bics[2]:.0f}, "
                   f"k3 {bics[3]:.0f} -> best k={best}")
        txt.append(f"  low-scale component: weight {est['w_low']:.3f} (95% CI {row['w_low_ci_lo']:.3f}-{row['w_low_ci_hi']:.3f}), "
                   f"mode 10^{est['mean_low']:.2f} (CI {row['mean_low_ci_lo']:.2f}-{row['mean_low_ci_hi']:.2f}), sd {est['sd_low']:.2f}")
        txt.append(f"  high-scale component: weight {est['w_high']:.3f}, mode 10^{est['mean_high']:.2f} "
                   f"(CI {row['mean_high_ci_lo']:.2f}-{row['mean_high_ci_hi']:.2f}), sd {est['sd_high']:.2f}")
        txt.append(f"  boundary 10^{est['boundary']:.2f} (CI {row['boundary_ci_lo']:.2f}-{row['boundary_ci_hi']:.2f}); KDE dip "
                   f"10^{est['kde_dip']:.2f} (CI {row['kde_dip_ci_lo']:.2f}-{row['kde_dip_ci_hi']:.2f}); share below boundary "
                   f"{100 * est['share_below_boundary']:.1f}% (CI {100 * row['share_below_boundary_ci_lo']:.1f}-"
                   f"{100 * row['share_below_boundary_ci_hi']:.1f}%)")
        txt.append(f"  excluded: {row['excluded_negative']} with negative values, {row['excluded_no_positive']} without positive values")
    pd.DataFrame(bic_rows).to_csv(OUT / "scale_mixture_bic.tsv", sep="\t", index=False, float_format="%.2f")
    pd.DataFrame(rows).to_csv(OUT / "scale_mixture_summary.tsv", sep="\t", index=False, float_format="%.4f")
    dr = d[d.modelled].groupby("source").dynamic_range.describe(percentiles=[.25, .5, .75])[["count", "25%", "50%", "75%"]]
    txt += ["", "dynamic range log10(p95/p5), modelled analyses:", dr.round(2).to_string()]
    (OUT / "scale_mixture_summary.txt").write_text("\n".join(txt) + "\n")
    print("\n".join(txt))


if __name__ == "__main__":
    main()
