"""ML Task Readiness (v9 tab), per analysis x endpoint, on the ML-eligible sample IDs in the classes chosen in Label structure
(the same classes: labels_v9.sample_classes). Decided by the author on 2026-10-23; revised 2026-10-24.

Scored (0-100, higher = better for ML):
  class separability = 100 x mean over classes of (1 - R(C)), the class R-value of Oh S., "A new dataset evaluation method based on
      category overlap", Comput Biol Med 41(2):115-122, 2011 (eq. 16): R(C) = share of the class's samples with more than theta of
      their k nearest neighbours in another class. Class-averaged so that imbalance cannot inflate it; ties at the k-th distance resolved
      exactly (hypergeometric over the tied samples); the chance level (expected score under random labels with the same class sizes)
      is reported beside it. k = 7 and theta = 3 by default (Oh's values on ten real datasets); 0 <= theta <= k/2. Not assessed when a
      class has fewer than k - theta + 1 samples, with at most k samples, or when a chosen class has no sample in the matrix.
Informational:
  overlap per class R(C) with its own chance level, the most overlapping pair R(Ci, Cj) (eqs. 14-15), samples certainly surrounded by
      another class (the idea of label purity, Gupta et al., Data Quality Toolkit, arXiv 2108.05935);
  features associated with the selected classes: share of the features tested whose values differ between the classes (the idea of
      feature relevance, Gupta et al.), Benjamini-Hochberg false discovery rate q. Without confirmed subjects: Kruskal-Wallis on one
      value per sample ID, a feature tested only when every class has at least 5 values (SciPy documentation). With the deposited or
      edited subject IDs confirmed by the user (2026-10-24, by_subject): Kruskal-Wallis on one value per subject when no subject spans
      classes; the exact Wilcoxon signed-rank test on complete subject pairs for two classes; Friedman only where SciPy states its
      p-value is reliable (more than 10 subjects and more than 6 classes); otherwise not assessable with subject adjustment;
  features per sample (p / n) and subject IDs as counts.
Unit-agnostic: every feature is ranked across the measured positions of the matrix (missing values take the mean rank), the ranks are
standardised per feature, one profile per sample ID is the mean of its positions, and distances are Euclidean on the profiles (computed
in float64, stored as float32). Distances and dense ranks are computed once per matrix (scripts/add_separability_from_deposit.py) and
stored; a class choice, a sample edit, k, theta, q or the subject confirmation only reselects rows.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from merit import labels_v9 as lb
from merit import structural_v9 as sv

PARAMETERS: dict = {"sep_k": (7.0, 3.0, 15.0, 1.0), "sep_theta": (3.0, 0.0, 7.0, 1.0), "sep_fdr": (0.05, 0.01, 0.20, 0.01)}
DEFAULT_PARAMS = {k: v[0] for k, v in PARAMETERS.items()}
FULL_LIMIT = 4000            # matrices with more sample IDs keep each sample ID's NN_KEEP nearest neighbours instead
NN_KEEP = 200
TIE_REL = 1e-5               # distances equal within this relative tolerance are tied (stored float32 rounding, at most 6e-8)
MIN_PER_CLASS = 5            # Kruskal-Wallis: a feature is judged only when every class has at least 5 values (SciPy documentation)
NO_SUBJECT = sv.NO_SUBJECT
TOP_N = 10                   # features associated with the classes: the significant features with the smallest p, kept by name (3 shown, all 10 on request)
FRIEDMAN_MIN_BLOCKS, FRIEDMAN_MIN_CLASSES = 11, 7    # SciPy friedmanchisquare: "the p-value is only reliable for n > 10 and more than 6 repeated samples"


def settings(params: dict | None) -> tuple[int, int, float]:
    p = params or {}
    def num(key):
        lo, hi = PARAMETERS[key][1], PARAMETERS[key][2]
        try:
            x = float(p.get(key, DEFAULT_PARAMS[key]))
        except (TypeError, ValueError):
            x = DEFAULT_PARAMS[key]
        return max(lo, min(hi, x))
    k = int(round(num("sep_k")))
    return k, min(int(round(num("sep_theta"))), k // 2), num("sep_fdr")


# ---------------------------------------------------------------------------------------------------------------------------------
# build time: one matrix -> distances (all measured positions) and dense ranks
# ---------------------------------------------------------------------------------------------------------------------------------
def standardised_ranks(mat: np.ndarray) -> np.ndarray:
    """Per feature: average ranks of the non-missing values, missing = the mean rank, then centred and scaled (constant -> 0)."""
    from scipy.stats import rankdata
    x = np.asarray(mat, dtype=np.float64)
    r = rankdata(x, axis=0, nan_policy="omit") if x.size else x
    mean = np.nanmean(r, axis=0) if r.size else np.zeros(x.shape[1])
    r = np.where(np.isnan(r), mean, r)
    r -= r.mean(axis=0)
    sd = r.std(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(sd > 0, r / np.where(sd > 0, sd, 1), 0.0)
    return z


def distances(z: np.ndarray) -> np.ndarray:
    """Square Euclidean distance matrix, returned as float32 but computed in float64: Gram products in row blocks, and pairs whose
    squared distance is tiny next to their squared norms (near-identical profiles, where the Gram form cancels) recomputed from
    direct differences, so that equal distances stay equal to float32 rounding."""
    z = np.asarray(z, dtype=np.float64); n = len(z)
    sq = np.einsum("ij,ij->i", z, z)
    out = np.empty((n, n), dtype=np.float32)
    for s in range(0, n, 1024):
        e = min(s + 1024, n)
        norms = sq[s:e, None] + sq[None, :]
        d2 = np.maximum(norms - 2 * (z[s:e] @ z.T), 0.0)
        rows, cols = np.nonzero(d2 < 1e-6 * norms)
        for c in range(0, len(rows), 4096):
            r, q = rows[c:c + 4096], cols[c:c + 4096]
            d2[r, q] = ((z[s + r] - z[q]) ** 2).sum(axis=1)
        out[s:e] = np.sqrt(d2)
    np.fill_diagonal(out, 0.0)
    return out


def dense_ranks(mat: np.ndarray) -> np.ndarray:
    """Per feature: 1, 2, ... for the distinct values in increasing order (ties equal), 0 = missing; uint16."""
    from scipy.stats import rankdata
    x = np.asarray(mat, dtype=np.float64)
    if not x.size:
        return np.zeros(x.shape, dtype=np.uint16)
    r = rankdata(x, method="dense", axis=0, nan_policy="omit")
    return np.where(np.isnan(r), 0, r).astype(np.uint16)


# ---------------------------------------------------------------------------------------------------------------------------------
# page time
# ---------------------------------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=6)
def _store(path: str) -> dict:
    with np.load(path, allow_pickle=False) as z:
        d = {k: z[k] for k in z.files}
    if "dist" in d:
        d["square"] = _square(d.pop("dist"), len(d["ids"]))
    return d


def _square(condensed: np.ndarray, n: int) -> np.ndarray:
    sq = np.zeros((n, n), dtype=np.float32)
    iu = np.triu_indices(n, 1)
    sq[iu] = condensed; sq[(iu[1], iu[0])] = condensed
    return sq


def condensed(square: np.ndarray) -> np.ndarray:
    return square[np.triu_indices(len(square), 1)].astype(np.float32)


def unit_profiles(z: np.ndarray, unit_of_row: np.ndarray, n_units: int) -> np.ndarray:
    """One profile per sample ID: the mean of the standardised-rank profiles of its measured positions (order-independent)."""
    out = np.zeros((n_units, z.shape[1]), dtype=np.float64)
    np.add.at(out, unit_of_row, z)
    counts = np.bincount(unit_of_row, minlength=n_units).astype(np.float64)
    return out / counts[:, None]


def _neighbour_sets(store: dict, units: np.ndarray, k: int):
    """For each chosen unit: (strictly closer neighbours, neighbours tied at the k-th distance, free slots among the tied), as boolean
    masks over the chosen units, so that no row order decides a tie. None when a large matrix's kept neighbour lists are not enough."""
    m = len(units)
    if "square" in store:
        sub = store["square"][np.ix_(units, units)].astype(np.float64)
        np.fill_diagonal(sub, np.inf)
        dk = np.partition(sub, k - 1, axis=1)[:, k - 1][:, None]
        tol = TIE_REL * np.maximum(dk, 1e-12)
        closer = sub < dk - tol; tie = np.abs(sub - dk) <= tol
        return closer, tie, k - closer.sum(axis=1)
    where = {int(u): i for i, u in enumerate(units)}
    closer = np.zeros((m, m), dtype=bool); tie = np.zeros((m, m), dtype=bool)
    for i, u in enumerate(units):
        cand = [(float(d), where[int(j)]) for j, d in zip(store["nn_idx"][u], store["nn_dist"][u]) if int(j) in where]
        if len(cand) < k or (len(store["nn_idx"][u]) == NN_KEEP and abs(cand[k - 1][0] - float(store["nn_dist"][u][-1])) <= TIE_REL * max(cand[k - 1][0], 1e-12)):
            return None
        dk = cand[k - 1][0]; tol = TIE_REL * max(dk, 1e-12)
        for d, j in cand:
            if d < dk - tol: closer[i, j] = True
            elif abs(d - dk) <= tol: tie[i, j] = True
    return closer, tie, k - closer.sum(axis=1)


def overlap(closer: np.ndarray, tie: np.ndarray, slots: np.ndarray, cls: np.ndarray, theta: int, k: int) -> dict:
    """Oh's R-value levels (eqs. 14-17) with an order-independent tie rule: when several samples tie at the k-th distance, the free
    slots are filled at random among them, and each sample's chance of being in an overlap area is computed exactly (hypergeometric)
    instead of letting row order decide. A sample's contribution is that probability (0 or 1 when there is no tie)."""
    from scipy.stats import hypergeom
    names = sorted(set(cls.tolist()))
    onehot = np.stack([cls == c for c in names], axis=1).astype(np.int64)
    in_s = closer.astype(np.int64) @ onehot; in_t = tie.astype(np.int64) @ onehot
    own = np.array([names.index(c) for c in cls])
    s = closer.sum(axis=1); t = tie.sum(axis=1)
    other_s = s - in_s[np.arange(len(cls)), own]; other_t = t - in_t[np.arange(len(cls)), own]
    p_in = hypergeom.sf(theta - other_s, t, other_t, slots)                 # P(other-class neighbours > theta)
    p_all = np.where(other_s == s, hypergeom.pmf(slots, t, other_t, slots), 0.0)   # all k neighbours in other classes
    per = {c: float(p_in[cls == c].mean()) for c in names}
    pairs = {}
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            ia, ib = cls == names[a], cls == names[b]
            r_ab = hypergeom.sf(theta - in_s[ia, b], t[ia], in_t[ia, b], slots[ia]).sum()
            r_ba = hypergeom.sf(theta - in_s[ib, a], t[ib], in_t[ib, a], slots[ib]).sum()
            pairs[(names[a], names[b])] = float((r_ab + r_ba) / (ia.sum() + ib.sum()))
    return {"r": float(p_in.mean()), "in_overlap": float(p_in.sum()), "per_class": per, "pairs": pairs,
            "surrounded": np.flatnonzero(p_all >= 1 - 1e-12), "tied_rows": int((t > slots).sum())}


def chance(counts: dict, k: int, theta: int) -> float:
    """Expected class separability under random labels with the same class sizes: each sample's k neighbours then carry labels drawn
    without replacement from the other samples' labels (hypergeometric), exactly, whatever the distances."""
    from scipy.stats import hypergeom
    n = sum(counts.values())
    return 100.0 * float(np.mean([1 - hypergeom.sf(theta, n - 1, n - c, k) for c in counts.values()]))


def chance_per_class(counts: dict, k: int, theta: int) -> dict:
    """Per class: the expected share of its samples outside an overlap area (x 100) under random labels with the same class sizes."""
    from scipy.stats import hypergeom
    n = sum(counts.values())
    return {c: 100.0 * float(1 - hypergeom.sf(theta, n - 1, n - m, k)) for c, m in counts.items()}


def discriminative(x: np.ndarray, cls: np.ndarray, q: float) -> dict:
    """Kruskal-Wallis per feature on the values within the chosen samples (one value per sample ID, missing left out, tie-corrected),
    judged only when every class has at least 5 values (SciPy: "each sample must have at least 5 measurements"); Benjamini-Hochberg."""
    from scipy.stats import chi2, rankdata
    x = np.asarray(x, dtype=np.float64)
    n_f = x.shape[1]
    if not n_f:
        return {"judged": 0, "significant": 0, "share": None}
    r = rankdata(x, axis=0, nan_policy="omit")
    ok = ~np.isnan(r)
    n = ok.sum(axis=0).astype(np.float64)
    term = np.zeros(n_f); smallest = np.full(n_f, np.inf); groups = 0; mean_rank = {}
    for c in sorted(set(cls.tolist())):
        m = cls == c; groups += 1
        ng = ok[m].sum(axis=0).astype(np.float64)
        sg = np.nansum(r[m], axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_rank[c] = sg / ng
        with np.errstate(invalid="ignore", divide="ignore"):
            term += np.where(ng > 0, sg * sg / np.where(ng > 0, ng, 1), 0.0)
        smallest = np.minimum(smallest, ng)
    with np.errstate(invalid="ignore", divide="ignore"):
        h = 12.0 / (n * (n + 1)) * term - 3 * (n + 1)
    # tie correction: 1 - sum(t^3 - t) / (n^3 - n), t = sizes of the groups of equal values within a feature; accumulated down the
    # sorted column (a run growing from t to t + 1 adds 3t^2 + 3t), so memory stays at the size of the matrix
    xs = np.sort(np.where(ok, x, np.inf), axis=0)
    ties = np.zeros(n_f); run = np.ones(n_f)
    for i in range(1, xs.shape[0]):
        same = (xs[i] == xs[i - 1]) & np.isfinite(xs[i])
        ties += np.where(same, 3 * run * run + 3 * run, 0.0)
        run = np.where(same, run + 1, 1.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = 1 - ties / (n ** 3 - n)
        h = h / corr
    judged = (smallest >= MIN_PER_CLASS) & (corr > 0) & np.isfinite(h)
    p = chi2.sf(h[judged], groups - 1)
    m = int(judged.sum())
    if not m:
        return {"judged": 0, "significant": 0, "share": None, "not_judged": int(n_f)}
    sig = _bh(p, q)
    return {"judged": m, "significant": sig, "share": 100.0 * sig / m, "not_judged": int(n_f - m),
            "top": _top(np.flatnonzero(judged), p, sig, lambda j: max(mean_rank, key=lambda c: (mean_rank[c][j], c)))}


def unit_values(ranks: np.ndarray, rows_of_unit: list) -> np.ndarray:
    """Per chosen sample ID: the mean of its positions' dense ranks (missing = 0 left out); NaN when all are missing."""
    out = np.full((len(rows_of_unit), ranks.shape[1]), np.nan)
    for i, rows in enumerate(rows_of_unit):
        v = np.asarray(ranks[rows], dtype=np.float64)
        v[v == 0] = np.nan
        if len(rows) == 1:
            out[i] = v[0]
        else:
            with np.errstate(all="ignore"):
                out[i] = np.nanmean(v, axis=0)
    return out


def subjects(roster: list, ids: list, positions: list, classes: list, overrides: dict | None = None) -> dict:
    """Informational counts of the subject IDs of the chosen sample IDs (as deposited, or as edited by the user; no interpretation): how
    many distinct, the most shared one and how many sample IDs carry it, how many carry several sample IDs, how many span several classes."""
    by: dict[str, list] = {}
    for s, p, c in zip(ids, positions, classes):
        name = sv.subject_of(roster[p], (overrides or {}).get(s))
        if name:
            by.setdefault(name, []).append(c)
    top = max(by.items(), key=lambda kv: (len(kv[1]), kv[0])) if by else None
    return {"subjects": len(by), "sample_ids_with_subject": sum(len(v) for v in by.values()),
            "subjects_with_several_samples": sum(1 for v in by.values() if len(v) > 1),
            "subjects_in_several_classes": sum(1 for v in by.values() if len(set(v)) > 1),
            "most_shared_subject": {"subject": top[0], "sample_ids": len(top[1])} if top else None}


def subjects_confirmed(params: dict | None) -> bool:
    """The user's confirmation, in Adjust Matrix Properties, that the subject IDs (deposited or edited) are real subjects."""
    return str((params or {}).get("subjects_confirmed", "") or "").strip().lower() in {"1", "true", "yes", "on"}


def signed_rank_p(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two-sided exact p of the Wilcoxon signed-rank test per column of d (pairs x features), zeros and missing values dropped (SciPy's
    zero_method 'wilcox'), tied absolute differences given average ranks: the null distribution of the sum of positive ranks over all
    2^n sign choices, counted by a subset sum over the doubled (integer) ranks. This is the distribution SciPy's exhaustive permutation
    enumerates for small samples with ties, computed without enumerating. Returns (p, pairs used per feature)."""
    from scipy.stats import rankdata
    d = np.asarray(d, dtype=np.float64)
    p = np.full(d.shape[1], np.nan); used = np.zeros(d.shape[1], dtype=np.int64)
    for j in range(d.shape[1]):
        x = d[:, j]; x = x[np.isfinite(x) & (x != 0)]
        n = x.size; used[j] = n
        if not n:
            continue
        r2 = np.rint(2 * rankdata(np.abs(x))).astype(np.int64)          # doubled average ranks are integers (>= 2)
        total = int(r2.sum()); w = int(r2[x > 0].sum())
        dist = np.zeros(total + 1, dtype=np.float64); dist[0] = 1.0
        for v in r2:                                                       # sign choices giving each sum of positive ranks
            dist[v:] = dist[v:] + dist[:-v].copy()
        dist /= 2.0 ** n
        p[j] = min(1.0, 2 * float(dist[:min(w, total - w) + 1].sum()))   # symmetric two-sided tail
    return p, used


def min_pairs(q: float) -> int:
    """Fewest pairs for which the exact two-sided signed-rank p can reach q: the smallest attainable p with n pairs is 2 / 2^n."""
    return max(1, math.ceil(1 + math.log2(1 / q)))


def bh_adjusted(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values over all the tested features: min over j >= i of p_(j) m / j, at most 1."""
    p = np.asarray(p, dtype=np.float64); m = p.size
    if not m:
        return p
    o = np.argsort(p, kind="stable")
    adj = np.minimum.accumulate((p[o] * m / np.arange(1, m + 1))[::-1])[::-1]
    out = np.empty(m); out[o] = np.minimum(adj, 1.0)
    return out


def _top(index: np.ndarray, p: np.ndarray, significant: int, highest) -> list:
    """The significant features with the smallest p (equal p to 12 significant digits: feature order), at most TOP_N, each with its p,
    its Benjamini-Hochberg adjusted p over every tested feature, and the class where it is highest by the test's own ordering (mean rank;
    within subjects for paired and blocked tests). Class medians of the deposited values are added in compute()."""
    key = np.array([float(f"{x:.12g}") for x in p])            # equal p (to 12 significant digits) -> feature order
    order = np.lexsort((index, key))[:min(TOP_N, significant)]
    q = bh_adjusted(p)
    return [{"index": int(index[i]), "p": float(p[i]), "q": float(q[i]), "highest": highest(int(index[i]))} for i in order]


def deposited_values(store_path: str | Path):
    """The deposited values of a matrix (positions x features, roster order), kept with the redundant-features store; None if not kept."""
    p = Path(store_path); cor = p.parents[2] / "correlation" / p.parent.name / p.name
    try:
        with np.load(cor) as z:
            return z["values"] if "values" in z.files else None
    except (OSError, KeyError, ValueError):
        return None


def feature_names(store_path: str | Path) -> list:
    """Feature names of a matrix, in column order: kept with the redundant-features store (correlation/<ST>/<AN>_<endpoint>.npz)."""
    p = Path(store_path); cor = p.parents[2] / "correlation" / p.parent.name / p.name
    try:
        with np.load(cor) as z:
            return [str(x) for x in z["names"]]
    except (OSError, KeyError, ValueError):
        return []


def _bh(p: np.ndarray, q: float) -> int:
    m = p.size
    if not m:
        return 0
    ps = np.sort(p)
    passed = np.flatnonzero(ps <= q * np.arange(1, m + 1) / m)
    return int(passed[-1] + 1) if passed.size else 0


def by_subject(x: np.ndarray, cls: np.ndarray, unit: list, q: float) -> dict:
    """Features associated with the classes when the subject IDs are confirmed as subjects. x: one value per chosen sample ID (rows) and
    feature; unit: its subject, or its own sample ID when it has none. Designs:
      no subject spans classes -> one value per subject (mean of its sample IDs' values) and Kruskal-Wallis on subjects ('between');
      subjects span classes, two classes -> exact Wilcoxon signed-rank on the subjects with a sample ID in both classes (the mean of its
          sample IDs in each), a feature tested with at least min_pairs(q) non-zero pairs ('paired');
      subjects span classes, three or more -> Friedman on subjects present in every class, only with more than 10 such subjects and
          more than 6 classes (SciPy's condition for its p-value); otherwise not assessable;
      some subjects span classes while others have several sample IDs in one class only ('mixed') -> not assessable.
    Missing values are never filled: a subject without a value in a class is left out of that feature's test."""
    classes = sorted(set(cls.tolist())); k = len(classes); n_f = x.shape[1]
    units = list(dict.fromkeys(unit)); col = {c: i for i, c in enumerate(classes)}
    rows_of: dict = {}
    for i, (u, c) in enumerate(zip(unit, cls.tolist())):
        rows_of.setdefault(u, {}).setdefault(c, []).append(i)
    spanning = [u for u in units if len(rows_of[u]) > 1]
    repeated_in_one = [u for u in units if len(rows_of[u]) == 1 and len(next(iter(rows_of[u].values()))) > 1]
    base = {"judged": 0, "significant": 0, "share": None, "not_judged": int(n_f), "adjusted": True, "subjects": len(units),
            "sample_ids": int(x.shape[0])}

    def mean_rows(rows):
        if len(rows) == 1:
            return x[rows[0]]
        with np.errstate(all="ignore"):
            return np.nanmean(x[rows], axis=0)
    if not spanning:
        xu = np.vstack([mean_rows(sum(rows_of[u].values(), [])) for u in units]) if units else np.zeros((0, n_f))
        cu = np.array([next(iter(rows_of[u])) for u in units], dtype=object)
        r = discriminative(xu, cu, q)
        return base | r | {"design": "between", "test": "Kruskal–Wallis on one value per subject"}
    if repeated_in_one:
        return base | {"design": "mixed", "not_assessable": (f"{len(spanning):,} subjects have sample IDs in several classes while {len(repeated_in_one):,} "
                                                            "have several sample IDs in one class only: this design is not assessable with subject adjustment")}
    complete = [u for u in units if len(rows_of[u]) == k]
    left_out = len(units) - len(complete)
    out = base | {"design": "paired" if k == 2 else "blocked", "complete_subjects": len(complete), "left_out_subjects": left_out}
    if k == 2:
        need = min_pairs(q)
        if len(complete) < need:
            return out | {"not_assessable": f"{len(complete):,} subjects have a sample ID in both classes; at least {need} are needed for the exact paired test"}
        a = np.vstack([mean_rows(rows_of[u][classes[0]]) for u in complete]); b = np.vstack([mean_rows(rows_of[u][classes[1]]) for u in complete])
        dif = a - b
        p, used = signed_rank_p(dif)
        judged = (used >= need) & np.isfinite(p)
        m = int(judged.sum()); sig = _bh(p[judged], q)

        def higher(j):                       # the class whose values are higher: the sign of the signed-rank sum
            from scipy.stats import rankdata
            x = dif[:, j]; x = x[np.isfinite(x) & (x != 0)]; rk = rankdata(np.abs(x))
            return classes[0] if rk[x > 0].sum() >= rk[x < 0].sum() else classes[1]
        return out | {"test": "exact Wilcoxon signed-rank test on subjects with a sample ID in both classes", "min_pairs": need,
                      "judged": m, "significant": sig, "share": (100.0 * sig / m) if m else None,
                      "top": _top(np.flatnonzero(judged), p[judged], sig, higher),
                      "not_judged": int(n_f - m), "pairs_per_feature": {"median": float(np.median(used)), "min": int(used.min()), "max": int(used.max())} if n_f else None}
    if len(complete) < FRIEDMAN_MIN_BLOCKS or k < FRIEDMAN_MIN_CLASSES:
        return out | {"not_assessable": (f"{len(complete):,} subjects have a sample ID in every one of the {k} classes; the Friedman test's p-value is reliable "
                                         f"only with more than 10 subjects and more than 6 classes (SciPy). Choose two classes for an exact paired test")}
    from scipy.stats import friedmanchisquare
    blocks = np.stack([np.vstack([mean_rows(rows_of[u][c]) for c in classes]) for u in complete])     # subjects x classes x features
    ps = np.full(n_f, np.nan); used = np.zeros(n_f, dtype=np.int64)
    for j in range(n_f):
        ok = np.isfinite(blocks[:, :, j]).all(axis=1); used[j] = int(ok.sum())
        if used[j] >= FRIEDMAN_MIN_BLOCKS:
            with np.errstate(all="ignore"):
                ps[j] = friedmanchisquare(*blocks[ok, :, j].T).pvalue
    judged = np.isfinite(ps)
    m = int(judged.sum()); sig = _bh(ps[judged], q)
    from scipy.stats import rankdata

    def highest_within(j):                   # mean within-subject rank, over the subjects the test used for this feature (complete ones)
        ok = np.isfinite(blocks[:, :, j]).all(axis=1)
        return classes[int(np.argmax(rankdata(blocks[ok, :, j], axis=1).mean(axis=0)))]
    return out | {"test": "Friedman test on subjects with a sample ID in every class", "judged": m, "significant": sig,
                  "share": (100.0 * sig / m) if m else None, "not_judged": int(n_f - m),
                  "top": _top(np.flatnonzero(judged), ps[judged], sig, highest_within)}


def _usable(vals: np.ndarray, cls: np.ndarray, counts: dict) -> int:
    """Readiness task gate: features with at least MIN_PER_CLASS values (one per sample ID) in every class."""
    if not vals.shape[1]:
        return 0
    have = np.vstack([np.isfinite(vals[cls == c]).sum(axis=0) for c in counts])
    return int((have >= MIN_PER_CLASS).all(axis=0).sum())


def compute(roster: list, study_id: str, store_path: str | Path, n_features: int, overrides: dict | None = None, choice: dict | None = None,
            params: dict | None = None, conflicts: dict | None = None, store: dict | None = None) -> dict:
    k, theta, q = settings(params)
    assigned = lb.sample_classes(roster, study_id, overrides, choice, conflicts)
    st = store if store is not None else _store(str(store_path))
    unit = {str(u): i for i, u in enumerate(st["ids"])}
    ids = [s for s in assigned if s in unit]
    units = np.array([unit[s] for s in ids], dtype=np.int64)
    cls = np.array([assigned[s][0] for s in ids], dtype=object)
    counts = {c: int((cls == c).sum()) for c in sorted(set(cls.tolist()))}
    out = {"k": k, "theta": theta, "fdr": q, "samples": len(ids), "classes": counts, "n_features": int(n_features),
           "features_per_sample": (n_features / len(ids)) if ids else None, "score": None, "chance": None, "not_assessed": None}
    out.update(subjects(roster, ids, [assigned[s][1] for s in ids], [assigned[s][0] for s in ids], overrides))
    out["subjects_confirmed"] = subjects_confirmed(params)
    if choice is not None:                  # the requested comparison: every chosen class must be present, as in Label structure
        empty = [lb.class_name(choice["factors"], v) for v in choice["classes"] if lb.class_name(choice["factors"], v) not in counts]
        if empty:
            out["not_assessed"] = f"no ML-eligible sample of {', '.join(empty)} in this matrix"
            return out
    if len(counts) < 2:
        out["not_assessed"] = "fewer than two classes with ML-eligible samples"
        return out
    if int(st.get("informative", 1)) == 0:
        out["not_assessed"] = "no feature varies across the measured samples of this matrix"
        rk_path = Path(str(store_path).replace(".npz", "_ranks.npy"))
        if rk_path.is_file():                # the readiness gate is still counted: values present, even if none varies
            rk = np.load(rk_path, mmap_mode="r"); ptr = st["unit_ptr"]; rows = st["unit_rows"]
            out["usable_features"] = _usable(unit_values(rk, [rows[ptr[u]:ptr[u + 1]] for u in units]), cls, counts)
        return out
    need = k - theta + 1
    if len(ids) > k:                         # the chance level needs k neighbours for every sample
        out["chance"] = chance(counts, k, theta)
        out["chance_per_class"] = chance_per_class(counts, k, theta)
    if min(counts.values()) < need:
        out["not_assessed"] = f"smallest class has {min(counts.values())} samples; at least {need} are needed (k = {k}, θ = {theta})"
    elif len(ids) <= k:
        out["not_assessed"] = f"{len(ids)} samples; more than k = {k} are needed"
    else:
        sets = _neighbour_sets(st, units, k)
        if sets is None:
            out["not_assessed"] = f"for this large matrix only each sample's {NN_KEEP} nearest neighbours are kept, and this class choice leaves fewer than k of them"
        else:
            ov = overlap(*sets, cls, theta, k)
            worst = max(ov["pairs"].items(), key=lambda kv: kv[1]) if ov["pairs"] else None
            out.update({"score": 100.0 * float(np.mean([1 - v for v in ov["per_class"].values()])),     # class-averaged (Oh eq. 16)
                        "r_value": ov["r"], "samples_outside_overlap": 100.0 * (1 - ov["r"]), "in_overlap": ov["in_overlap"],
                        "per_class": ov["per_class"], "worst_pair": {"classes": list(worst[0]), "r": worst[1]} if worst else None,
                        "surrounded": len(ov["surrounded"]), "surrounded_ids": [ids[i] for i in ov["surrounded"]][:200],
                        "tied_samples": ov["tied_rows"]})
    ranks_path = Path(str(store_path).replace(".npz", "_ranks.npy"))
    if ranks_path.is_file():
        rk = np.load(ranks_path, mmap_mode="r")
        ptr = st["unit_ptr"]; rows = st["unit_rows"]
        vals = unit_values(rk, [rows[ptr[u]:ptr[u + 1]] for u in units])
        if len(counts) >= 2:
            out["usable_features"] = _usable(vals, cls, counts)
        out["discriminative"] = discriminative(vals, cls, q) | {"adjusted": False}
        if out["subjects_confirmed"] and out["subjects"]:
            unit_of = [sv.subject_of(roster[assigned[s][1]], (overrides or {}).get(s)) or "\x00" + s for s in ids]   # no subject: its own unit
            out["discriminative_unadjusted"] = out["discriminative"]
            out["discriminative"] = by_subject(vals, cls, unit_of, q)
        top = out["discriminative"].get("top") or []
        names = feature_names(store_path) if top else []
        for t in top:
            t["name"] = names[t["index"]] if t["index"] < len(names) else f"feature {t['index'] + 1}"
        if top:
            unit_of = ([sv.subject_of(roster[assigned[s][1]], (overrides or {}).get(s)) or "\x00" + s for s in ids]
                       if out["discriminative"].get("adjusted") else list(ids))
            _medians(top, deposited_values(store_path), st, units, cls, unit_of, out["discriminative"].get("design"))
    return out


def _medians(top: list, values, st: dict, units: np.ndarray, cls: np.ndarray, unit_of: list, design: str | None) -> None:
    """Class medians of the deposited values of each top feature, over the units the test used: sample IDs (each the mean of its
    positions), subjects (the mean of their sample IDs) for a between-subject test, or, for a paired or blocked test, the subjects that
    have a value of that feature in every class (the same feature-specific mask as the test), each subject's mean in each class. Without confirmed subjects and for the between-subject test, the class with the highest median is the
    'higher in' class when it is unique (else the test's mean-rank class stays); paired and blocked tests keep the within-subject class."""
    if values is None:
        for t in top:
            t["medians"] = None
        return
    pos = st["pos"]; ptr = st["unit_ptr"]; rows = st["unit_rows"]; cols = [t["index"] for t in top]
    per_id = np.vstack([np.nanmean(np.asarray(values[pos[rows[ptr[u]:ptr[u + 1]]]][:, cols], dtype=np.float64), axis=0)
                        if ptr[u + 1] - ptr[u] > 1 else np.asarray(values[pos[rows[ptr[u]]]][cols], dtype=np.float64) for u in units])
    classes = sorted(set(cls.tolist())); cells: dict = {}
    for i, (u, c) in enumerate(zip(unit_of, cls.tolist())):
        cells.setdefault((u, c), []).append(i)
    if design in ("paired", "blocked"):
        complete = [u for u in dict.fromkeys(unit_of) if all((u, c) in cells for c in classes)]
        keys = [(u, c) for u in complete for c in classes]
    else:
        keys = list(cells)
    with np.errstate(all="ignore"):
        unit_vals = {k: np.nanmean(per_id[cells[k]], axis=0) for k in keys}
    for n, t in enumerate(top):
        med = {}; cnt = {}
        if design in ("paired", "blocked"):
            # the subjects this feature's test used: a value in every class (the same mask as the paired and Friedman tests)
            used = [u for u in complete if all(np.isfinite(unit_vals[(u, c)][n]) for c in classes)]
            use = [(u, c) for u in used for c in classes]
        else:
            use = keys
        for c in classes:
            v = np.array([unit_vals[k][n] for k in use if k[1] == c]); v = v[np.isfinite(v)]
            med[c] = float(np.median(v)) if v.size else None; cnt[c] = int(v.size)
        t["medians"] = med; t["n"] = cnt
        if design not in ("paired", "blocked"):
            known = {c: m for c, m in med.items() if m is not None}
            best = max(known.values()) if known else None
            if best is not None and sum(1 for m in known.values() if m == best) == 1:
                t["highest"] = next(c for c, m in known.items() if m == best); t["by"] = "median"
            else:
                t["by"] = "mean rank"
        else:
            t["by"] = "within subjects"
