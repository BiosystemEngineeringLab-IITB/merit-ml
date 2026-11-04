"""Structural items of one matrix (one analysis from one endpoint), computed from its position roster.

One function serves the v9 cache builder, the page and the download: `compute(roster, params, overrides)`.
With default parameters and no sample edits it returns exactly what the cache stores.

A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
ML-eligible positions are the positions that are not Tier 1 sample alignment rows and not QC/blank; a sample edit can
mark a sample ID ML-eligible, not ML-eligible, or leave it out of the matrix. A Tier 1 sample alignment row holds no
measured value and can never be made ML-eligible.
"""
from __future__ import annotations

import collections
from typing import Any

# one roster entry per position, in file order
ROSTER_FIELDS = ("sample_id", "label_on_position", "qc_blank", "tier1_alignment", "in_sample_table", "label_in_sample_table",
                 "subject_id", "numeric_cells", "missing_entry_cells", "text_cells")
I_ID, I_LABEL, I_QC, I_ALIGN, I_TABLE, I_TLABEL, I_SUBJECT, I_NUM, I_MISS, I_TEXT = range(10)

ITEM_KEYS = ("sample_support", "samples_with_factor", "numeric_values", "unique_sample_ids")
EMPTY_VALUE = {"", "-"}      # an empty field, or the repository's '-' for "nothing entered"; words such as None or NA are deposited values
NO_SUBJECT = {"", "-", "na", "n/a", "nan", "null", "none", "unknown", "not applicable"}

# parameters a user can change in the left panel: key -> (default, lowest, highest, step)
PARAMETERS = {"structural_sample_support_n": (20.0, 5.0, 100.0, 1.0)}
DEFAULT_PARAMS = {key: spec[0] for key, spec in PARAMETERS.items()}


def label_values(text: Any) -> list[str]:
    return [(part.split(":", 1)[1] if ":" in part else part).strip() for part in str(text).split("|")]


def nonempty_label(text: Any) -> bool:
    """A factor label counts when at least one of its values is not empty ('Factor:Value | Factor:Value' or plain text)."""
    return any(v not in EMPTY_VALUE for v in label_values(text))


def all_values_filled(text: Any) -> bool:
    values = label_values(text)
    return bool(values) and all(v not in EMPTY_VALUE for v in values)


def subject_of(row: list, edit: dict | None = None) -> str:
    """The subject of a position: the user's edit when there is one ('-' = none), else the deposited subject ID; '' = none."""
    edited = str((edit or {}).get("subject", "") or "").strip()
    value = edited if edited else str(row[I_SUBJECT] or "").strip()
    return "" if value.casefold() in NO_SUBJECT else value


def effective_positions(roster: list[list], overrides: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every position with its status and label after the sample edits (keyed by sample ID)."""
    overrides = overrides or {}
    out = []
    for row in roster:
        edit = overrides.get(row[I_ID]) or {}
        excluded = bool(edit.get("excluded")) and not row[I_ALIGN]
        default_eligible = not row[I_QC] and not row[I_ALIGN]
        eligible = bool(edit["eligible"]) if "eligible" in edit else default_eligible
        eligible = eligible and not excluded and not row[I_ALIGN]
        deposited = row[I_LABEL] if nonempty_label(row[I_LABEL]) else (row[I_TLABEL] if row[I_TABLE] else "")
        label = edit["label"] if str(edit.get("label", "") or "").strip() else deposited
        subject = subject_of(row, edit)
        out.append({"row": row, "sample_id": row[I_ID], "eligible": eligible, "excluded": excluded, "label": label, "subject": subject,
                    "edited": eligible != default_eligible or excluded or label != deposited or subject != subject_of(row)})
    return out


def compute(roster: list[list], params: dict[str, float] | None = None,
            overrides: dict[str, dict[str, Any]] | None = None, sample_table_lines: int | None = None) -> dict[str, Any]:
    """The four scored items, the Structural score and the counts behind them."""
    full_marks = float((params or {}).get("structural_sample_support_n", DEFAULT_PARAMS["structural_sample_support_n"]))
    positions = effective_positions(roster, overrides)
    kept = [p for p in positions if not p["excluded"]]
    measured = [p for p in kept if not p["row"][I_ALIGN]]
    elig = [p for p in measured if p["eligible"]]
    ids = [p["sample_id"] for p in elig]
    count = collections.Counter(ids)
    distinct = list(count)
    rec: dict[str, Any] = {
        "positions": len(roster),
        "tier1_alignment_rows": sum(1 for row in roster if row[I_ALIGN]),
        "excluded_positions": len(positions) - len(kept),
        "measured_positions": len(measured),
        "qc_blank_positions": len(measured) - len(elig),
        "eligible_positions": len(elig),
        "distinct_eligible_ids": len(distinct),
        "edited_positions": sum(1 for p in positions if p["edited"]),
        "sample_support_full_marks_at": full_marks,
    }
    rec["sample_support"] = min(1.0, len(distinct) / full_marks) if ids else None
    rec["unique_sample_ids"] = len(distinct) / len(ids) if ids else None
    rec["repeated_positions"] = len(ids) - len(distinct)
    rec["positions_with_repeated_id"] = sum(c for c in count.values() if c > 1)
    rec["sample_ids_repeated"] = sum(1 for c in count.values() if c > 1)
    rec["repeated_id_examples"] = [f"{i} ×{c}" for i, c in count.most_common(5) if c > 1]
    labelled = [nonempty_label(p["label"]) for p in elig]
    rec["samples_with_factor"] = sum(labelled) / len(elig) if elig else None
    rec["n_with_factor"] = sum(labelled)
    rec["samples_without_factor_examples"] = [p["sample_id"] for p, ok in zip(elig, labelled) if not ok][:8]
    rec["every_factor_value_filled"] = sum(all_values_filled(p["label"]) for p in elig) / len(elig) if elig else None
    numeric = sum(p["row"][I_NUM] for p in elig); missing = sum(p["row"][I_MISS] for p in elig); text = sum(p["row"][I_TEXT] for p in elig)
    rec.update(value_cells=numeric + missing + text, numeric_cells=numeric, missing_entry_cells=missing, text_cells=text)
    rec["numeric_values"] = (1 - text / (numeric + text)) if (numeric + text) else None
    has_table = any(row[I_TABLE] for row in roster) if sample_table_lines is None else bool(sample_table_lines)
    in_table = {p["sample_id"] for p in elig if p["row"][I_TABLE]}
    rec["sample_ids_in_sample_table"] = sum(1 for i in distinct if i in in_table) / len(distinct) if distinct and has_table else None
    # subject IDs of the ML-eligible sample IDs (sample table, first field, or the user's edit): information only
    subject_by_id: dict[str, str] = {}
    for p in elig:
        subject_by_id.setdefault(p["sample_id"], p["subject"])
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for sample_id, subject in subject_by_id.items():
        if subject:
            groups[subject].append(sample_id)
    several = {s: v for s, v in groups.items() if len(v) > 1}
    rec["subject_ids_given_for"] = sum(len(v) for v in groups.values())
    rec["distinct_subject_ids"] = len(groups)
    rec["subjects_with_several_sample_ids"] = len(several)
    rec["sample_ids_sharing_a_subject"] = sum(len(v) for v in several.values())
    rec["subject_examples"] = [{"subject_id": s, "n_sample_ids": len(v), "sample_ids": v[:5]}
                               for s, v in sorted(several.items(), key=lambda kv: -len(kv[1]))[:3]]
    scored = [rec[k] for k in ITEM_KEYS if rec[k] is not None]
    rec["structural"] = sum(scored) / len(scored) if scored else None
    rec["n_scored_items"] = len(scored)
    return rec

