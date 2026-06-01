from __future__ import annotations

from collections import Counter

from merit.models import CanonicalStudy, MetricResult
from merit.utils import is_usable_class_label, normalize_label, sample_object_is_biological

from .base import MetricPlugin

def _is_biological_sample(sample: object) -> bool:
    return sample_object_is_biological(sample)


class LabelSuitabilityMetric(MetricPlugin):
    family = "ML Task Readiness"
    name = "label_suitability"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        labels = []
        for sample in study.samples:
            if not _is_biological_sample(sample):
                continue
            if not is_usable_class_label(sample.label):
                continue
            labels.append(normalize_label(sample.label))
        counts = Counter(labels)
        min_count = study.score_defaults.get("minimum_class_count", 5)
        if len(counts) < 2:
            score = 0.0
        else:
            observed_min = min(counts.values())
            score = min(1.0, observed_min / min_count)
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 1.0 else "warn",
            summary=f"Label suitability score is {score:.3f} with class counts {dict(counts)}.",
            details={"counts": dict(counts), "minimum_class_count": min_count},
            thresholds={"minimum_class_count": min_count},
            recommendations=[] if score >= 1.0 else ["Increase minority-class size or merge labels before benchmarking."],
        )


class LeakageRiskMetric(MetricPlugin):
    family = "ML Task Readiness"
    name = "benchmark_split_leakage_risk"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        total_sample_appearances = 0
        duplicate_occurrences = 0
        repeated_within_assays: dict[str, dict[str, int]] = {}
        per_analysis_summary: list[dict[str, int | str]] = []
        flattened_repeats: dict[str, int] = {}

        for matrix in study.feature_matrices:
            assay_id = str(matrix.assay_id or matrix.matrix_id or "unknown")
            counts = Counter(matrix.sample_ids)
            repeats = {sample_id: count for sample_id, count in counts.items() if count > 1}
            dup_occ = sum(count - 1 for count in repeats.values())
            total_sample_appearances += len(matrix.sample_ids)
            duplicate_occurrences += dup_occ
            if repeats:
                repeated_within_assays[assay_id] = repeats
                for sample_id, count in repeats.items():
                    flattened_repeats[f"{assay_id}::{sample_id}"] = count
            per_analysis_summary.append(
                {
                    "analysis_id": assay_id,
                    "n_rows": len(matrix.sample_ids),
                    "n_unique_sample_ids": len(counts),
                    "n_duplicated_ids": len(repeats),
                    "duplicate_occurrences": dup_occ,
                }
            )

        denominator = max(1, total_sample_appearances)
        score = max(0.0, 1.0 - duplicate_occurrences / denominator)
        assays_with_duplicates = sum(1 for item in per_analysis_summary if int(item["duplicate_occurrences"]) > 0)
        duplicated_ids = sum(int(item["n_duplicated_ids"]) for item in per_analysis_summary)
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.85 else "warn",
            summary=(
                f"Detected {duplicate_occurrences} duplicate sample-ID occurrences within assay matrices "
                f"({duplicated_ids} duplicated IDs across {assays_with_duplicates}/{len(per_analysis_summary)} analyses)."
            ),
            details={
                "duplicate_occurrences_within_assays": duplicate_occurrences,
                "total_sample_appearances": total_sample_appearances,
                "per_analysis_duplicate_summary": per_analysis_summary,
                "repeated_samples_within_assays": repeated_within_assays,
                # Backward-compatible key for older UI/report consumers.
                "repeated_samples": flattened_repeats,
            },
            thresholds={"recommended_minimum": 0.85},
            recommendations=[] if score >= 0.85 else [
                "Deduplicate repeated sample IDs within each assay matrix before train/test splitting."
            ],
        )


class FeatureToSampleRatioMetric(MetricPlugin):
    family = "ML Task Readiness"
    name = "feature_to_sample_ratio"

    def _ratio_score(self, ratio: float) -> float:
        if ratio <= 10:
            return 1.0
        if ratio <= 50:
            return 0.8
        if ratio <= 200:
            return 0.5
        return max(0.1, 1.0 - ratio / 1000)

    def compute(self, study: CanonicalStudy) -> MetricResult:
        n_bio = len([
            s for s in study.samples
            if _is_biological_sample(s)
        ])

        if n_bio == 0 or not study.feature_matrices:
            return MetricResult(
                family=self.family, name=self.name, score=0.0, status="warn",
                summary="Cannot compute ratio: no samples or features.",
                details={}, thresholds={}, recommendations=[],
            )

        # Compute per-matrix ratio and score individually, then combine via
        # sample-weighted mean so larger assays (the ones more likely to be
        # used for ML) contribute proportionally more.
        per_analysis: list[dict] = []
        weighted_score_sum = 0.0
        weight_sum = 0
        worst_ratio = 0.0

        for matrix in study.feature_matrices:
            n_features = len(matrix.feature_ids)
            n_samples_in_matrix = len(matrix.sample_ids) or n_bio
            if n_features == 0:
                continue
            # Per-analysis ratio: features vs samples actually present in this
            # matrix (not the study-level n_bio), so large multi-assay studies
            # are not penalised by analyses with few samples.
            ratio = n_features / n_samples_in_matrix
            matrix_score = self._ratio_score(ratio)
            per_analysis.append({
                "analysis_id": matrix.assay_id,
                "n_features_in_matrix": n_features,
                "n_samples_in_matrix": n_samples_in_matrix,
                "ratio": round(ratio, 2),
                "score": round(matrix_score, 3),
            })
            weighted_score_sum += matrix_score * n_samples_in_matrix
            weight_sum += n_samples_in_matrix
            worst_ratio = max(worst_ratio, ratio)

        if weight_sum == 0:
            return MetricResult(
                family=self.family, name=self.name, score=0.0, status="warn",
                summary="Cannot compute ratio: no features in any matrix.",
                details={}, thresholds={}, recommendations=[],
            )

        score = weighted_score_sum / weight_sum
        n_features_total = sum(len(m.feature_ids) for m in study.feature_matrices)

        # Per-analysis p/n ratios for summary statistics
        pn_ratios = [item["ratio"] for item in per_analysis if item.get("n_samples_in_matrix", 0) > 0]
        pn_ratios_sorted = sorted(pn_ratios)
        if pn_ratios_sorted:
            n_pn = len(pn_ratios_sorted)
            median_pn = pn_ratios_sorted[n_pn // 2] if n_pn % 2 == 1 else (pn_ratios_sorted[n_pn // 2 - 1] + pn_ratios_sorted[n_pn // 2]) / 2.0
            pct_pn_gt1 = round(100.0 * sum(1 for r in pn_ratios if r > 1) / n_pn, 1)
        else:
            median_pn = None
            pct_pn_gt1 = None

        global_ratio = round(n_features_total / n_bio, 2) if n_bio > 0 else None
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.8 else "warn",
            summary=(
                f"Feature-to-sample ratio (sample-weighted across {len(per_analysis)} matrices): "
                f"worst {worst_ratio:.1f}:1, median p/n {median_pn:.1f}:1, composite score {score:.3f}."
                if median_pn is not None else
                f"Feature-to-sample ratio (sample-weighted across {len(per_analysis)} matrices): "
                f"worst {worst_ratio:.1f}:1, composite score {score:.3f}."
            ),
            details={
                "n_biological_samples": n_bio,
                "n_matrices": len(per_analysis),
                # primary keys expected by UI tooltip
                "total_features": n_features_total,
                "ratio": global_ratio,
                # legacy key kept for older consumers
                "n_features_total_all_matrices": n_features_total,
                "worst_ratio": round(worst_ratio, 2),
                "median_pn_ratio": round(median_pn, 2) if median_pn is not None else None,
                "pct_analyses_pn_gt1": pct_pn_gt1,
                "composite_score": round(score, 3),
                "aggregation": "sample_weighted_mean",
                "per_analysis": per_analysis,
            },
            thresholds={"low_risk": 10, "moderate_risk": 50, "high_risk": 200},
            recommendations=[] if score >= 0.8 else [
                f"High feature-to-sample ratio (worst {worst_ratio:.0f}:1). Use regularised models (LASSO, Ridge, Elastic Net) "
                "or apply feature selection before training."
            ],
        )


class RecommendedMLTaskMetric(MetricPlugin):
    family = "ML Task Readiness"
    name = "recommended_ml_task"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        labels = [
            normalize_label(s.label)
            for s in study.samples
            if _is_biological_sample(s) and is_usable_class_label(s.label)
        ]
        valid = [lb for lb in labels if lb]
        counts = Counter(valid)
        n_classes = len(counts)

        if n_classes == 0:
            task, score = "undetermined", 0.0
            detail = "No usable labels found."
            recs = ["Define a label variable to enable ML task recommendation."]
        elif n_classes == 1:
            task, score = "single_class_only", 0.1
            detail = "Only one class found — supervised classification is not possible."
            recs = ["Add a control/comparison group to enable binary classification."]
        elif n_classes == 2:
            task, score = "binary_classification", 1.0
            detail = f"Two classes: {list(counts.keys())}."
            recs = []
        elif n_classes <= 10:
            task, score = "multi_class_classification", 1.0
            detail = f"{n_classes} classes detected."
            recs = []
        elif n_classes <= 20:
            task, score = "high_cardinality_classification", 0.3
            detail = f"{n_classes} classes detected — verify labels are categorical, not continuous."
            recs = ["Consider whether some label groups can be merged to improve per-class sample sizes."]
        else:
            task, score = "excessive_classes", 0.0
            detail = f"{n_classes} distinct labels — likely continuous values or unstandardised factor strings."
            recs = ["Consolidate label categories or treat as a regression task if labels are continuous."]

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.8 else ("warn" if score >= 0.5 else "fail"),
            summary=f"Recommended ML task: {task.replace('_', ' ')}. {detail}",
            details={"task": task, "n_classes": n_classes, "class_counts": dict(counts.most_common(10))},
            thresholds={},
            recommendations=recs,
        )


class StratifiedSplitFeasibilityMetric(MetricPlugin):
    family = "ML Task Readiness"
    name = "stratified_split_feasibility"
    TEST_SIZE = 0.25
    MIN_CLASS_COUNT = 5

    def compute(self, study: CanonicalStudy) -> MetricResult:
        labels = [
            normalize_label(s.label)
            for s in study.samples
            if _is_biological_sample(s) and is_usable_class_label(s.label)
        ]
        valid = [lb for lb in labels if lb]
        counts = Counter(valid)

        if len(counts) < 2:
            return MetricResult(
                family=self.family, name=self.name, score=0.0, status="warn",
                summary="Cannot assess split: fewer than 2 label classes.",
                details={}, thresholds={},
                recommendations=["Define at least 2 label groups before attempting train/test splits."],
            )

        infeasible = []
        for cls, n in counts.items():
            test_n = max(1, int(n * self.TEST_SIZE))
            train_n = n - test_n
            if test_n < self.MIN_CLASS_COUNT or train_n < self.MIN_CLASS_COUNT:
                infeasible.append({"class": cls, "n": n, "test_n": test_n, "train_n": train_n})

        score = 1.0 - len(infeasible) / len(counts)
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if not infeasible else "warn",
            summary=(
                f"Stratified {int((1-self.TEST_SIZE)*100)}/{int(self.TEST_SIZE*100)} split "
                f"{'feasible for all classes' if not infeasible else f'infeasible for {len(infeasible)} class(es)'}."
            ),
            details={"infeasible_classes": infeasible, "test_size": self.TEST_SIZE, "min_class_count": self.MIN_CLASS_COUNT},
            thresholds={"minimum_class_count_per_split": self.MIN_CLASS_COUNT},
            recommendations=[
                f"Classes {[i['class'] for i in infeasible]} have too few samples for a stratified split. "
                "Consider oversampling or merging minority classes."
            ] if infeasible else [],
        )
