from __future__ import annotations

import re
from collections import Counter

from pathlib import Path

from merit import manuscript_feature_labels as ms_labels
from merit.feature_names import classify_feature_name
from merit.models import CanonicalStudy, MetricResult

from .base import MetricPlugin


def _nmr_binned_fraction(study: CanonicalStudy) -> float:
    """Return fraction of annotations whose feature name is an NMR spectral bin."""
    total = len(study.annotations)
    if not total:
        return 0.0
    nmr_count = sum(
        1 for a in study.annotations
        if classify_feature_name(a.raw_name or "").get("kind") in {"nmr_bin", "nmr_bin_range"}
    )
    return nmr_count / total


def _is_nmr_binned(study: CanonicalStudy, threshold: float = 0.5) -> bool:
    return _nmr_binned_fraction(study) >= threshold

_MZ_RT_PATTERN = re.compile(
    # Common mz/RT encodings:
    #  - mz_rt, mz/rt, mz_rt_charge triplets
    #  - rt_mz with optional mode suffix (e.g. 0.03_85.0528n, 0.00_121.0076m/z)
    #  - M1234.5T6.7 format
    #  - mz1234.5 / m1234.5 format
    r"^\d+\.?\d*[_/]\d+\.?\d*(?:[_/]\d+\.?\d*)?(?:m/?z|mz|[np])?$"
    r"|^[Mm]\d+\.?\d*[Tt]\d+\.?\d*$"
    r"|^[Mm][Zz]?\s*\d+\.?\d*$",
    re.IGNORECASE,
)
_UNKNOWN_TERMS = {
    "unknown", "na", "n/a", "", "unidentified", "unnamed",
    "not identified", "unknown metabolite", "unknown compound",
    "unassigned", "unannotated", "noname", "no name",
}
_TRUSTED_MAPPING_NAMESPACES = {
    "refmet",
    "hmdb",
    "chebi",
    "kegg",
    "pubchem",
    "inchi",
    "inchikey",
    "metlin",
    "lipidmaps",
}


class IdentifierCoverageMetric(MetricPlugin):
    family = "Annotation / Interoperability"
    name = "identifier_coverage"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        annotations = study.annotations

        # NMR binned studies use ppm chemical-shift bins — external DB IDs are
        # structurally inapplicable. Return N/A rather than a misleading zero.
        if _is_nmr_binned(study):
            nmr_frac = _nmr_binned_fraction(study)
            return MetricResult(
                family=self.family,
                name=self.name,
                score=1.0,
                status="pass",
                summary=(
                    f"Identifier mapping not applicable: {nmr_frac:.0%} of features are "
                    "NMR chemical-shift bins. External DB identifiers (HMDB, RefMet) "
                    "cannot be assigned to spectral bins."
                ),
                details={
                    "covered": 0,
                    "total": len(annotations),
                    "coverage_mode": "nmr_binned_not_applicable",
                    "nmr_bin_fraction": round(nmr_frac, 4),
                },
                thresholds={"recommended_minimum": 0.7},
                recommendations=[],
            )

        if not annotations:
            score = 0.0
            covered = 0
        else:
            # Strict coverage: count only external IDs or explicitly non-lexical
            # mapping namespaces (e.g. refmet). Do not count default lexical
            # name normalization, even if mapping_confidence is high.
            mappings = study.mappings or []
            covered = 0
            namespace_breakdown: Counter[str] = Counter()
            for idx, annotation in enumerate(annotations):
                namespace = ""
                if idx < len(mappings):
                    namespace = (mappings[idx].namespace or "").strip().lower()
                has_external_db_id = bool((annotation.database_identifier or "").strip())
                is_trusted_mapping = namespace in _TRUSTED_MAPPING_NAMESPACES
                if has_external_db_id or is_trusted_mapping:
                    covered += 1
                    namespace_breakdown[namespace or "database_id_only"] += 1
            score = covered / len(annotations)
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.7 else "warn",
            summary=f"{covered}/{len(annotations)} annotations have external identifiers or non-lexical reference mappings.",
            details={
                "covered": covered,
                "total": len(annotations),
                "coverage_mode": "strict_external_only",
                "namespaces": dict(namespace_breakdown) if annotations else {},
            },
            thresholds={"recommended_minimum": 0.7},
            recommendations=[] if score >= 0.7 else ["Expand identifier mapping to improve pathway-level reuse and cross-study merges."],
        )


class AnnotationAmbiguityMetric(MetricPlugin):
    family = "Annotation / Interoperability"
    name = "annotation_ambiguity_burden"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        # NMR bins cannot be ambiguous in the metabolite-identity sense.
        if _is_nmr_binned(study):
            nmr_frac = _nmr_binned_fraction(study)
            return MetricResult(
                family=self.family,
                name=self.name,
                score=1.0,
                status="pass",
                summary=(
                    f"Ambiguity assessment not applicable: {nmr_frac:.0%} of features are "
                    "NMR chemical-shift bins. Bins are unambiguous spectral positions."
                ),
                details={
                    "ambiguous": 0,
                    "total": len(study.annotations),
                    "coverage_mode": "nmr_binned_not_applicable",
                    "nmr_bin_fraction": round(nmr_frac, 4),
                },
                thresholds={"recommended_minimum": 0.7},
                recommendations=[],
            )
        total = len(study.annotations)
        ambiguous = 0
        flag_counts: Counter[str] = Counter()
        examples_by_reason: dict[str, list[str]] = {
            "semicolon_delimited": [],
            "slash_delimited": [],
            "refmet_match_count": [],
        }

        def _push_example(bucket: str, name: str) -> None:
            text = str(name or "").strip()
            if not text:
                return
            slot = examples_by_reason[bucket]
            if text in slot:
                return
            if len(slot) < 10:
                slot.append(text)

        for annotation in study.annotations:
            flags = [str(flag).strip().lower() for flag in (annotation.ambiguity_flags or []) if str(flag).strip()]
            if not flags:
                continue
            ambiguous += 1
            for flag in flags:
                flag_counts[flag] += 1
            raw_name = str(annotation.raw_name or "").strip()
            if "multi_candidate_name_semicolon" in flags:
                _push_example("semicolon_delimited", raw_name)
            if "multi_candidate_name_slash" in flags:
                _push_example("slash_delimited", raw_name)
            if "multi_candidate_name_refmet" in flags:
                _push_example("refmet_match_count", raw_name)

        # Build a mixed top-10 list by round-robin across reason buckets.
        mixed_top10: list[dict[str, str]] = []
        order = ("semicolon_delimited", "slash_delimited", "refmet_match_count")
        indices = {key: 0 for key in order}
        while len(mixed_top10) < 10:
            progressed = False
            for key in order:
                values = examples_by_reason.get(key, [])
                idx = indices[key]
                if idx >= len(values):
                    continue
                mixed_top10.append({"reason": key, "name": values[idx]})
                indices[key] = idx + 1
                progressed = True
                if len(mixed_top10) >= 10:
                    break
            if not progressed:
                break

        score = 1.0 - (ambiguous / total) if total else 0.0
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.7 else "warn",
            summary=f"{ambiguous}/{total} annotations are marked ambiguous.",
            details={
                "ambiguous": ambiguous,
                "total": total,
                "ambiguity_flag_counts": dict(flag_counts),
                "multi_candidate_examples": examples_by_reason,
                "multi_candidate_examples_mixed_top10": mixed_top10,
            },
            thresholds={"recommended_minimum": 0.7},
            recommendations=[] if score >= 0.7 else ["Flag unresolved isomers and unknowns before interpretation-heavy analyses."],
        )


class FeatureRedundancyMetric(MetricPlugin):
    family = "Annotation / Interoperability"
    name = "feature_redundancy"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        feature_to_assay: dict[str, str] = {}
        for matrix in study.feature_matrices:
            assay_id = str(matrix.assay_id or "").strip()
            for feature_id in matrix.feature_ids:
                fid = str(feature_id or "").strip()
                if fid:
                    feature_to_assay[fid] = assay_id

        assay_to_analysis: dict[str, str] = {}
        for assay in study.assays:
            assay_id = str(assay.assay_id or "").strip()
            if not assay_id:
                continue
            analysis_id = ""
            if isinstance(assay.metadata, dict):
                analysis_id = str(assay.metadata.get("analysis_id") or "").strip()
            if not analysis_id:
                analysis_id = str(assay.name or "").strip()
            if not analysis_id:
                analysis_id = assay_id
            assay_to_analysis[assay_id] = analysis_id

        # Build per-assay name→locations index.  The same metabolite name
        # appearing across *different* assays (e.g. positive and negative mode)
        # is expected and should not be penalised.  Only count redundancy
        # *within* a single assay.
        name_locations_by_assay: dict[str, dict[str, list[dict[str, str]]]] = {}
        name_locations_global: dict[str, list[dict[str, str]]] = {}
        for annotation in study.annotations:
            raw_name = str(annotation.raw_name or "").strip()
            if not raw_name:
                continue
            feature_id = str(annotation.feature_id or "").strip()
            assay_id = feature_to_assay.get(feature_id, "")
            analysis_id = assay_to_analysis.get(assay_id, "")
            if not analysis_id and "::" in feature_id:
                analysis_id = feature_id.split("::", 1)[0]
            location = {
                "feature_id": feature_id,
                "assay_id": assay_id,
                "analysis_id": analysis_id,
            }
            assay_key = assay_id or analysis_id or "__global__"
            name_locations_by_assay.setdefault(assay_key, {}).setdefault(raw_name, []).append(location)
            name_locations_global.setdefault(raw_name, []).append(location)

        # Count within-assay redundancy only.
        redundant = 0
        total = 0
        within_assay_duplicate_items: list[tuple[str, int, str]] = []
        for assay_key, name_map in name_locations_by_assay.items():
            for name, locations in name_map.items():
                total += len(locations)
                if len(locations) > 1:
                    redundant += len(locations) - 1
                    within_assay_duplicate_items.append((name, len(locations), assay_key))

        within_assay_duplicate_items.sort(key=lambda item: (-item[1], item[0]))
        score = 1.0 - (redundant / total) if total else 0.0

        # Build cross-assay detail for informational display.
        repeated_feature_locations: list[dict[str, object]] = []
        for name, count, assay_key in within_assay_duplicate_items[:20]:
            locations = name_locations_global.get(name, [])
            analysis_ids = sorted({
                str(loc.get("analysis_id") or "").strip()
                for loc in locations if str(loc.get("analysis_id") or "").strip()
            })
            repeated_feature_locations.append({
                "name": name,
                "within_assay_count": count,
                "assay_key": assay_key,
                "analysis_ids": analysis_ids,
            })

        top_redundant = {name: count for name, count, _ in within_assay_duplicate_items[:20]}
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.85 else "warn",
            summary=f"Detected {redundant} within-assay redundant feature names across {total} annotations.",
            details={
                "redundant": redundant,
                "duplicate_name_groups": len(within_assay_duplicate_items),
                "top_redundant": top_redundant,
                "repeated_feature_locations_top20": repeated_feature_locations,
                "redundancy_mode": "within_assay_raw_feature_name_exact",
            },
            thresholds={"recommended_minimum": 0.85},
            recommendations=[] if score >= 0.85 else ["Collapse near-duplicate or repeated annotations within each assay before modeling."],
        )


class FeatureAnnotationTypeMetric(MetricPlugin):
    family = "Annotation / Interoperability"
    name = "feature_annotation_type"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        named = mz_rt = nmr_bin = unannotated = non_metabolite = 0
        for annotation in study.annotations:
            raw = (annotation.raw_name or "").strip()
            raw_lower = raw.lower()
            if not raw or raw_lower in _UNKNOWN_TERMS:
                unannotated += 1
                continue
            feature_class = classify_feature_name(raw)
            kind = feature_class.get("kind", "")
            if kind in {"nmr_bin", "nmr_bin_range"}:
                nmr_bin += 1
            elif feature_class["is_mz_rt"] or _MZ_RT_PATTERN.match(raw.replace(" ", "")):
                mz_rt += 1
            elif feature_class["is_non_metabolite"]:
                non_metabolite += 1
                unannotated += 1
            else:
                named += 1

        total = named + mz_rt + nmr_bin + unannotated
        if total == 0:
            score, tier = 0.0, "no_annotations"
        else:
            nmr_frac = nmr_bin / total
            named_frac = named / total
            if nmr_frac >= 0.5:
                # NMR binned: spectral positions are structurally meaningful but
                # not named metabolites. Score 0.65 — usable for ML but limits
                # pathway/cross-study reuse.
                score, tier = 0.65, "nmr_binned"
            elif named_frac >= 0.7:
                score, tier = 1.0, "named_metabolites"
            elif named > 0 and (named + mz_rt) / total >= 0.7:
                score, tier = 0.5, "mixed_mz_rt"
            else:
                score, tier = 0.2, "mostly_unannotated"

        recommendations = []
        if tier == "nmr_binned":
            recommendations = [
                "NMR chemical-shift bins are suitable for within-study ML but cannot be "
                "mapped to metabolite databases or merged with MS-based studies. "
                "If targeted NMR assignments (metabolite names) are available, prefer those."
            ]
        elif score < 0.7:
            recommendations = [
                "Named metabolites are preferred for ML interpretability and cross-study reuse. "
                "Consider studies with higher annotation coverage."
            ]

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.65 else "warn",
            summary=(
                f"Annotation tier: {tier}. Named: {named}, mz/RT: {mz_rt}, "
                f"NMR bins: {nmr_bin}, Unannotated: {unannotated}, "
                f"Non-metabolite tokens: {non_metabolite}."
            ),
            details={
                "named": named,
                "mz_rt": mz_rt,
                "nmr_bin": nmr_bin,
                "unannotated": unannotated,
                "non_metabolite": non_metabolite,
                "total": total,
                "tier": tier,
            },
            thresholds={"named_fraction_for_pass": 0.7, "nmr_bin_fraction_for_tier": 0.5},
            recommendations=recommendations,
        )


class UnknownFeatureFractionMetric(MetricPlugin):
    family = "Annotation / Interoperability"
    name = "unknown_feature_fraction"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        total = len(study.annotations)
        unknown = 0
        nmr_bins = 0
        for a in study.annotations:
            raw = str(a.raw_name or "").strip()
            kind = classify_feature_name(raw).get("kind", "")
            if kind in {"nmr_bin", "nmr_bin_range"}:
                nmr_bins += 1  # bins are identified spectral positions, not "unknown"
            elif raw.lower() in _UNKNOWN_TERMS:
                unknown += 1
        score = 1.0 - (unknown / total) if total else 0.0
        nmr_note = f" ({nmr_bins} NMR spectral bins treated as identified.)" if nmr_bins else ""
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.8 else "warn",
            summary=f"{unknown}/{total} features have unknown or unidentified annotations.{nmr_note}",
            details={"unknown_features": unknown, "total_features": total, "nmr_bin_features": nmr_bins},
            thresholds={"recommended_maximum_unknown_fraction": 0.2},
            recommendations=[] if score >= 0.8 else [
                "High proportion of unknown features reduces interpretability. "
                "Cross-reference with spectral databases (HMDB, MassBank) to improve coverage."
            ],
        )


# ---------------------------------------------------------------------------------------------------------------------
# Annotation coverage with the rule set of the manuscript (Text S3, Table S1a-c). Added 2026-10-07.
# ---------------------------------------------------------------------------------------------------------------------
NOT_ASSESSABLE = "not_assessable"   # nothing to score: left out of the section mean (readiness_score._mean_score)
LABEL_RULE_SOURCE = (
    "Feature labels are read from the deposited file: the header cells after the sample and class columns "
    "(datatable, untarg_data), or the first cell of every row of the mwTab data block. They are classified with the "
    "label and context rules of the manuscript (Text S3, Table S1a-b)."
)
_LABEL_SUMMARY_CACHE: dict[tuple, list[dict]] = {}


def _matrix_file(study: CanonicalStudy, matrix) -> tuple[Path | None, str]:
    """(deposited file of an assessed matrix, source name used by the label rules)."""
    root = Path(str(study.provenance.source_root or "")).expanduser()
    study_id = str(study.study.study_id or "").strip().upper()
    analysis_id = str(matrix.assay_id or "").strip().upper()
    kind = str(matrix.source_kind or "").strip().lower()
    if kind == "datatable":
        return root / study_id / analysis_id / "tabular" / f"{analysis_id}_datatable_slash.txt", "datatable"
    if kind == "mwtab":
        return root / study_id / analysis_id / "mwtab" / f"{analysis_id}_mwtab.txt", "mwtab"
    if kind in {"untarg", "untarg_data"}:
        path = root / study_id / analysis_id / "tabular" / f"{analysis_id}_untarg_data.tsv"
        if not path.is_file():                             # analysis listed under two studies: one shared file
            path = next(root.glob(f"ST*/{analysis_id}/tabular/{analysis_id}_untarg_data.tsv"), None)
        return path, "untarg_data"
    return None, kind


def matrix_label_summaries(study: CanonicalStudy) -> list[dict]:
    """Final label classes of every assessed matrix (one entry per analysis), by the rules of the manuscript."""
    root = Path(str(study.provenance.source_root or "")).expanduser()
    study_id = str(study.study.study_id or "").strip().upper()
    cache_key = (str(root), study_id, tuple((str(m.assay_id), str(m.source_kind)) for m in study.feature_matrices))
    if cache_key in _LABEL_SUMMARY_CACHE:
        return _LABEL_SUMMARY_CACHE[cache_key]
    roster, rmap = ms_labels.study_context(root / study_id)
    out = []
    for matrix in study.feature_matrices:
        analysis_id = str(matrix.assay_id or "").strip().upper()
        path, source = _matrix_file(study, matrix)
        if path is None or not Path(path).is_file():
            out.append({"analysis_id": matrix.assay_id, "file_found": False})
            continue
        labels, factor_names = ms_labels.labels_of_file(Path(path), source)
        summary = ms_labels.classify_matrix(labels, analysis_id, factor_names, roster, rmap)
        summary.update({"analysis_id": matrix.assay_id, "file_found": True})
        out.append(summary)
    _LABEL_SUMMARY_CACHE.clear()                             # one study at a time
    _LABEL_SUMMARY_CACHE[cache_key] = out
    return out


def _share(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


class NamedFeatureCoverageMetric(MetricPlugin):
    """Named-feature coverage (manuscript: annotation coverage, Table 4, Table S1c, Figure 4C).

        coverage of an analysis = named features / eligible features of its matrix
        score of the source     = mean of the analysis coverages (every analysis counts equally)

    Eligible features are the feature labels that stand for a metabolite feature: all labels except protein entries,
    sample-metadata labels, empty labels and the labels of a corrupted matrix. An analysis without an eligible feature
    has no coverage and is left out of the mean; when no analysis has one the metric is "not_assessable". A matrix
    with eligible features of which none is named scores 0. The pooled value (all named / all eligible features of the
    source) is reported beside the score and is not the score. Replaces ``feature_annotation_type`` (tiered score) in
    the registry; the other annotation metrics are unchanged.
    """

    family = "Annotation / Interoperability"
    name = "named_feature_coverage"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        per_analysis = []
        totals: Counter = Counter()
        class_totals: Counter = Counter()
        for item in matrix_label_summaries(study):
            if not item.get("file_found"):
                per_analysis.append({"analysis_id": item["analysis_id"], "file_found": False, "coverage": None,
                                     "n_named": 0, "n_eligible": 0, "n_excluded": 0, "n_labels": 0})
                continue
            per_analysis.append({
                "analysis_id": item["analysis_id"],
                "file_found": True,
                "n_labels": item["n_labels"],
                "n_named": item["n_named"],
                "n_without_name": item["n_without_name"],
                "n_eligible": item["n_eligible"],
                "n_excluded": item["n_excluded"],
                "coverage": _share(item["n_named"], item["n_eligible"]),
                "class_counts": item["class_counts"],
                "class_examples": item["class_examples"],
                "corrupted_matrix": item["malformed_matrix"],
            })
            for key in ("n_labels", "n_named", "n_without_name", "n_eligible", "n_excluded", "n_named_lower", "n_named_upper"):
                totals[key] += item[key]
            class_totals.update(item["class_counts"])
        coverages = [x["coverage"] for x in per_analysis if x["coverage"] is not None]
        assessable = bool(coverages)
        score = (sum(coverages) / len(coverages)) if assessable else 0.0
        pooled = _share(totals["n_named"], totals["n_eligible"])
        if not assessable:
            summary = "Not assessable: no analysis has an eligible feature label."
            status = NOT_ASSESSABLE
        else:
            summary = (
                f"{totals['n_named']:,} of {totals['n_eligible']:,} eligible features carry a metabolite name "
                f"({100 * pooled:.1f}% pooled); mean of {len(coverages)} analysis coverages {100 * score:.1f}%."
                + (f" {totals['n_excluded']:,} labels excluded (not metabolite features)." if totals["n_excluded"] else "")
            )
            status = "pass" if score >= 0.70 else "warn"
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=summary,
            details={
                "not_assessable": not assessable,
                "n_analyses_scored": len(coverages),
                "n_analyses_without_eligible_feature": len(per_analysis) - len(coverages),
                "n_labels": totals["n_labels"],
                "n_named": totals["n_named"],
                "n_without_name": totals["n_without_name"],
                "n_eligible": totals["n_eligible"],
                "n_excluded": totals["n_excluded"],
                "pooled_coverage": pooled,
                # sensitivity bounds of Table S1c, pooled over the source
                "pooled_lower_bound": _share(totals["n_named_lower"], totals["n_eligible"]),
                "pooled_upper_bound": _share(totals["n_named_upper"], totals["n_eligible"]),
                "class_counts": dict(class_totals),
                "per_analysis": per_analysis,
                "score_definition": (
                    "per analysis: named features / eligible features; source score: mean of the analysis coverages; "
                    "an analysis without an eligible feature is left out"
                ),
                "label_rule_source": LABEL_RULE_SOURCE,
            },
            thresholds={"recommended_minimum": 0.70},
            recommendations=[] if (not assessable or score >= 0.70) else [
                "Features without a reported metabolite name can still be used for within-study ML. They need "
                "annotation before cross-study comparison or pathway analysis."
            ],
        )


class RefmetStandardizedNamedFeaturesMetric(MetricPlugin):
    """Informational: of the named features, the share that resolves to a valid, unique RefMet name of the same analysis.

    Per analysis: named features with a valid unique same-analysis RefMet name / named features; shown as the mean
    over the analyses that have a named feature. Not assessable when no analysis has a named feature. Not scored: the
    RefMet mapping is scored once, in Metadata and FAIR.
    """

    family = "Annotation / Interoperability"
    name = "refmet_standardized_named_features"
    informational = True

    def compute(self, study: CanonicalStudy) -> MetricResult:
        per_analysis = []
        n_named = n_resolved = 0
        for item in matrix_label_summaries(study):
            if not item.get("file_found"):
                continue
            per_analysis.append({
                "analysis_id": item["analysis_id"],
                "n_named": item["n_named"],
                "n_named_with_refmet": item["n_named_lower"],
                "share": _share(item["n_named_lower"], item["n_named"]),
            })
            n_named += item["n_named"]
            n_resolved += item["n_named_lower"]
        shares = [x["share"] for x in per_analysis if x["share"] is not None]
        assessable = bool(shares)
        score = (sum(shares) / len(shares)) if assessable else 0.0
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if assessable else NOT_ASSESSABLE,
            summary=(
                f"{n_resolved:,} of {n_named:,} named features resolve to a RefMet name of the same analysis "
                f"({100 * n_resolved / n_named:.1f}% pooled); mean of {len(shares)} analyses {100 * score:.1f}%."
                if assessable else "Not assessable: no analysis has a named feature."
            ),
            details={
                "not_assessable": not assessable,
                "n_analyses_with_named_features": len(shares),
                "n_named": n_named,
                "n_named_with_refmet": n_resolved,
                "pooled_share": _share(n_resolved, n_named),
                "per_analysis": per_analysis,
                "score_definition": (
                    "per analysis: named features with a valid unique RefMet name in the metabolite records of the "
                    "same analysis / named features; shown as the mean over analyses with a named feature"
                ),
            },
            recommendations=[],
        )
