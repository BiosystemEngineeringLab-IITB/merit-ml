from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

from merit.models import CanonicalStudy, MetricResult
from merit.utils import is_usable_class_label, normalize_label, sample_is_qc_like

from .analytical import _is_missing
from .base import MetricPlugin

def _is_biological_sample(sample_id: str, label: str, sample_type: str) -> bool:
    return not sample_is_qc_like(
        sample_id=sample_id,
        label=label,
        sample_type=sample_type,
        class_string=label,
    )


class ClassSeparabilityMetric(MetricPlugin):
    family = "Class Separability"
    name = "class_separability"
    profiles = ("full",)

    MIN_TOTAL_SAMPLES = 20
    MIN_CLASS_SAMPLES = 3
    MAX_FEATURES = 2000
    CV_REPEATS = 3
    CV_TEST_SIZE = 0.25
    MAX_PCA_POINTS = 2000

    def _label_for_sample(self, matrix: Any, sample_id: str, sample_lookup: dict[str, Any]) -> str:
        label = ""
        if isinstance(getattr(matrix, "labels", None), dict):
            label = str(matrix.labels.get(sample_id, "") or "").strip()
        if not label:
            sample = sample_lookup.get(sample_id)
            if sample is not None:
                label = str(sample.label or "").strip()
        return label

    def _build_matrix(
        self,
        matrix: Any,
        sample_lookup: dict[str, Any],
    ) -> tuple[np.ndarray | None, list[str], list[str], dict[str, int], str]:
        rows: list[list[float]] = []
        labels: list[str] = []
        sample_ids: list[str] = []

        for idx, row in enumerate(matrix.values):
            sample_id = matrix.sample_ids[idx] if idx < len(matrix.sample_ids) else f"row_{idx + 1}"
            raw_label = self._label_for_sample(matrix, sample_id, sample_lookup)
            if not is_usable_class_label(raw_label):
                continue
            label = normalize_label(raw_label)
            if not label:
                continue
            sample = sample_lookup.get(sample_id)
            sample_type = str((sample.sample_type if sample is not None else "") or "")
            if not _is_biological_sample(sample_id, raw_label, sample_type):
                continue
            parsed = [np.nan if _is_missing(value) else float(value) for value in row]
            rows.append(parsed)
            labels.append(label)
            sample_ids.append(sample_id)

        if len(rows) < self.MIN_TOTAL_SAMPLES:
            return None, [], [], {}, f"Need >= {self.MIN_TOTAL_SAMPLES} labeled ML-eligible samples."

        counts = Counter(labels)
        valid_classes = {label for label, n in counts.items() if n >= self.MIN_CLASS_SAMPLES}
        if len(valid_classes) < 2:
            return None, [], [], dict(counts), f"Need >=2 classes with at least {self.MIN_CLASS_SAMPLES} samples each."

        filtered = [
            (row, label, sample_id)
            for row, label, sample_id in zip(rows, labels, sample_ids)
            if label in valid_classes
        ]
        rows = [row for row, _, _ in filtered]
        labels = [label for _, label, _ in filtered]
        sample_ids = [sample_id for _, _, sample_id in filtered]
        counts = Counter(labels)
        if len(rows) < self.MIN_TOTAL_SAMPLES:
            return None, [], [], dict(counts), f"Need >= {self.MIN_TOTAL_SAMPLES} labeled ML-eligible samples after class filtering."

        width = max((len(row) for row in rows), default=0)
        if width == 0:
            return None, [], [], dict(counts), "No numeric feature values."

        x = np.full((len(rows), width), np.nan, dtype=float)
        for i, row in enumerate(rows):
            if row:
                x[i, : len(row)] = np.array(row, dtype=float)

        keep_cols = np.any(np.isfinite(x), axis=0)
        x = x[:, keep_cols]
        if x.shape[1] == 0:
            return None, [], [], dict(counts), "No finite feature columns after cleanup."

        med = np.nanmedian(x, axis=0)
        med = np.where(np.isfinite(med), med, 0.0)
        missing_idx = np.where(~np.isfinite(x))
        if missing_idx[0].size > 0:
            x[missing_idx] = med[missing_idx[1]]

        var = np.var(x, axis=0)
        non_constant = var > 1e-12
        x = x[:, non_constant]
        var = var[non_constant]
        if x.shape[1] == 0:
            return None, [], [], dict(counts), "All features are constant after imputation."

        if x.shape[1] > self.MAX_FEATURES:
            idx = np.argsort(var)[-self.MAX_FEATURES :]
            x = x[:, idx]

        mu = np.mean(x, axis=0)
        sigma = np.std(x, axis=0)
        keep = sigma > 1e-12
        x = x[:, keep]
        if x.shape[1] == 0:
            return None, [], [], dict(counts), "No variable features after scaling."
        x = (x - mu[keep]) / sigma[keep]

        return x, labels, sample_ids, dict(counts), ""

    def _pca_projection(self, x: np.ndarray, labels: list[str], sample_ids: list[str]) -> dict[str, Any]:
        if x.size == 0 or not labels or not sample_ids:
            return {}

        n_total = x.shape[0]
        if n_total == 0:
            return {}

        # Downsample very large cohorts while preserving class proportions.
        if n_total > self.MAX_PCA_POINTS:
            label_arr = np.asarray(labels, dtype=object)
            selected: list[int] = []
            classes = sorted(set(labels))
            quotas: dict[str, int] = {}
            total_assigned = 0
            for cls in classes:
                cls_idx = np.where(label_arr == cls)[0]
                q = max(1, int(round((len(cls_idx) / n_total) * self.MAX_PCA_POINTS)))
                q = min(q, len(cls_idx))
                quotas[cls] = q
                total_assigned += q
            # Adjust quota total to exact cap.
            while total_assigned > self.MAX_PCA_POINTS:
                cls = max(quotas, key=lambda c: quotas[c])
                if quotas[cls] > 1:
                    quotas[cls] -= 1
                    total_assigned -= 1
                else:
                    break
            while total_assigned < self.MAX_PCA_POINTS:
                candidates = [c for c in quotas if quotas[c] < int(np.sum(label_arr == c))]
                if not candidates:
                    break
                cls = max(candidates, key=lambda c: int(np.sum(label_arr == c)) - quotas[c])
                quotas[cls] += 1
                total_assigned += 1
            for cls in classes:
                cls_idx = np.where(label_arr == cls)[0]
                q = quotas.get(cls, 0)
                if q <= 0:
                    continue
                picks = np.linspace(0, len(cls_idx) - 1, num=q, dtype=int)
                selected.extend(cls_idx[picks].tolist())
            selected = sorted(set(selected))
            vis_idx = np.asarray(selected, dtype=int)
        else:
            vis_idx = np.arange(n_total, dtype=int)

        x_vis = x[vis_idx, :]
        labels_vis = [labels[i] for i in vis_idx]
        ids_vis = [sample_ids[i] for i in vis_idx]

        n_components = 2 if min(x_vis.shape[0], x_vis.shape[1]) >= 2 else 1
        pca = PCA(n_components=n_components, random_state=0)
        coords = pca.fit_transform(x_vis)
        if n_components == 1:
            pc1 = coords[:, 0]
            pc2 = np.zeros_like(pc1)
            evr = [float(pca.explained_variance_ratio_[0]) if pca.explained_variance_ratio_.size else 0.0, 0.0]
        else:
            pc1 = coords[:, 0]
            pc2 = coords[:, 1]
            evr_raw = pca.explained_variance_ratio_.tolist()
            evr = [float(evr_raw[0]) if len(evr_raw) > 0 else 0.0, float(evr_raw[1]) if len(evr_raw) > 1 else 0.0]

        return {
            "sample_ids": ids_vis,
            "labels": labels_vis,
            "pc1": [float(v) for v in pc1],
            "pc2": [float(v) for v in pc2],
            "explained_variance_ratio": evr,
            "n_points": int(len(ids_vis)),
            "n_total": int(n_total),
            "downsampled": bool(len(ids_vis) < n_total),
        }

    def _cv_linear_auroc(self, x: np.ndarray, labels: list[str]) -> tuple[float | None, float | None, str]:
        unique = sorted(set(labels))
        if len(unique) < 2:
            return None, None, "CV AUROC proxy requires at least two classes."

        label_to_int = {label: idx for idx, label in enumerate(unique)}
        y = np.asarray([label_to_int[label] for label in labels], dtype=int)
        if len(np.unique(y)) < 2:
            return None, None, "CV AUROC proxy requires two non-empty classes."

        aucs: list[float] = []
        for seed in range(self.CV_REPEATS):
            try:
                x_train, x_test, y_train, y_test = train_test_split(
                    x,
                    y,
                    test_size=self.CV_TEST_SIZE,
                    random_state=seed,
                    stratify=y,
                )
            except ValueError:
                return None, None, "CV AUROC proxy split failed due to class/sample constraints."

            if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
                continue

            model = LogisticRegression(max_iter=1000, random_state=seed)
            try:
                model.fit(x_train, y_train)
                y_prob = model.predict_proba(x_test)
                if len(unique) == 2:
                    auc = float(roc_auc_score(y_test, y_prob[:, 1]))
                else:
                    present = np.asarray(sorted(set(int(v) for v in y_test)), dtype=int)
                    if present.size < 2:
                        continue
                    y_prob_sub = y_prob[:, present]
                    denom = np.sum(y_prob_sub, axis=1, keepdims=True)
                    denom = np.where(denom > 0, denom, 1.0)
                    y_prob_sub = y_prob_sub / denom
                    mapping = {int(cls): idx for idx, cls in enumerate(present.tolist())}
                    y_test_mapped = np.asarray([mapping[int(v)] for v in y_test], dtype=int)
                    auc = float(
                        roc_auc_score(
                            y_test_mapped,
                            y_prob_sub,
                            multi_class="ovr",
                            average="macro",
                        )
                    )
                if np.isfinite(auc):
                    aucs.append(auc)
            except Exception:
                continue

        if not aucs:
            return None, None, "CV AUROC proxy could not be computed."
        return float(np.mean(aucs)), float(np.std(aucs)), ""

    def compute(self, study: CanonicalStudy) -> MetricResult:
        sample_lookup = {sample.sample_id: sample for sample in study.samples}
        per_analysis: list[dict[str, Any]] = []
        eligible_scores: list[float] = []

        for matrix in study.feature_matrices:
            x, labels, sample_ids, counts, reason = self._build_matrix(matrix, sample_lookup)
            analysis_id = str(matrix.assay_id or matrix.matrix_id or "unknown")
            if x is None or not labels:
                per_analysis.append(
                    {
                        "analysis_id": analysis_id,
                        "score": None,
                        "cv_linear_auroc_mean": None,
                        "cv_linear_auroc_std": None,
                        "n_samples_labeled": sum(counts.values()) if counts else 0,
                        "n_classes": len(counts),
                        "n_features_used": 0,
                        "class_counts": counts,
                        "class_labels": sorted(counts),
                        "pca_projection": {},
                        "eligible_for_auroc": False,
                        "skipped_reason": reason or "Insufficient labeled data.",
                    }
                )
                continue

            n_samples, n_features = x.shape
            class_labels = sorted(set(labels))

            cv_auc_mean, cv_auc_std, cv_reason = self._cv_linear_auroc(x, labels)
            analysis_score = float(cv_auc_mean) if cv_auc_mean is not None else None

            pca_projection = self._pca_projection(x, labels, sample_ids)

            eligible = analysis_score is not None
            if eligible:
                eligible_scores.append(float(analysis_score))

            per_analysis.append(
                {
                    "analysis_id": analysis_id,
                    "score": analysis_score,
                    "cv_linear_auroc_mean": cv_auc_mean,
                    "cv_linear_auroc_std": cv_auc_std,
                    "n_samples_labeled": n_samples,
                    "n_classes": len(class_labels),
                    "n_features_used": n_features,
                    "class_counts": counts,
                    "class_labels": class_labels,
                    "pca_projection": pca_projection,
                    "eligible_for_auroc": eligible,
                    "skipped_reason": cv_reason if not eligible else "",
                }
            )

        n_analyses_total = len(per_analysis)
        n_analyses_eligible = len(eligible_scores)
        n_analyses_ineligible = max(0, n_analyses_total - n_analyses_eligible)
        coverage = (n_analyses_eligible / n_analyses_total) if n_analyses_total > 0 else 0.0

        if not eligible_scores:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.0,
                status="warn",
                summary=(
                    "Class separability could not be computed: 0/"
                    f"{n_analyses_total} analyses eligible for AUROC."
                ),
                details={
                    "method": "cv_linear_auroc_diagnostic",
                    "formula": (
                        "analysis_score = mean(cv_auroc across stratified repeats) for eligible analyses only; "
                        "study_score = mean(analysis_score across eligible analyses); "
                        "binary AUROC for 2 classes, macro-OVR AUROC for multiclass."
                    ),
                    "aggregation": "unweighted_mean_eligible_analyses",
                    "max_features_evaluated": self.MAX_FEATURES,
                    "imputation": "median_per_feature",
                    "scaling": "zscore_per_feature",
                    "cv_repeats": self.CV_REPEATS,
                    "cv_test_size": self.CV_TEST_SIZE,
                    "n_analyses_total": n_analyses_total,
                    "n_analyses_eligible": 0,
                    "n_analyses_ineligible": n_analyses_ineligible,
                    "eligible_coverage": coverage,
                    "mean_cv_auroc_eligible": None,
                    "median_cv_auroc_eligible": None,
                    "iqr_cv_auroc_eligible": None,
                    "ci95_cv_auroc_eligible": [None, None],
                    "per_analysis": per_analysis,
                },
                thresholds={"pass": 0.7, "warn": 0.6},
                recommendations=["Add class labels with sufficient per-class sample counts to assess separability."],
            )

        score = float(np.mean(eligible_scores))
        median_auc = float(np.median(eligible_scores))
        q25 = float(np.percentile(eligible_scores, 25))
        q75 = float(np.percentile(eligible_scores, 75))
        iqr_auc = q75 - q25
        if n_analyses_eligible > 1:
            std_auc = float(np.std(eligible_scores, ddof=1))
            half_width = 1.96 * (std_auc / np.sqrt(float(n_analyses_eligible)))
        else:
            half_width = 0.0
        ci_low = max(0.0, score - half_width)
        ci_high = min(1.0, score + half_width)

        status = "pass" if score >= 0.7 else ("warn" if score >= 0.6 else "fail")
        summary = (
            f"Eligible analyses: {n_analyses_eligible}/{n_analyses_total} ({coverage:.1%}). "
            f"Mean CV linear-AUROC (eligible-only) = {score:.3f}; "
            f"median = {median_auc:.3f} (IQR={iqr_auc:.3f}), 95% CI [{ci_low:.3f}, {ci_high:.3f}]."
        )

        recs: list[str] = []
        if score < 0.6:
            recs.append(
                "Low AUROC-based class separability; expect weak class discrimination with linear models."
            )
        elif score < 0.7:
            recs.append("Moderate AUROC-based separability; validate carefully and consider targeted feature selection.")

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=summary,
            details={
                "method": "cv_linear_auroc_diagnostic",
                "formula": (
                    "analysis_score = mean(cv_auroc across stratified repeats) for eligible analyses only, "
                    "study_score = mean(analysis_score across eligible analyses); "
                    "binary AUROC for 2 classes, macro-OVR AUROC for multiclass."
                ),
                "aggregation": "unweighted_mean_eligible_analyses",
                "max_features_evaluated": self.MAX_FEATURES,
                "imputation": "median_per_feature",
                "scaling": "zscore_per_feature",
                "cv_repeats": self.CV_REPEATS,
                "cv_test_size": self.CV_TEST_SIZE,
                "n_analyses_total": n_analyses_total,
                "n_analyses_eligible": n_analyses_eligible,
                "n_analyses_ineligible": n_analyses_ineligible,
                "eligible_coverage": coverage,
                "mean_cv_auroc_eligible": score,
                "median_cv_auroc_eligible": median_auc,
                "iqr_cv_auroc_eligible": iqr_auc,
                "ci95_cv_auroc_eligible": [ci_low, ci_high],
                "per_analysis": per_analysis,
            },
            thresholds={"pass": 0.7, "warn": 0.6},
            recommendations=recs,
        )
