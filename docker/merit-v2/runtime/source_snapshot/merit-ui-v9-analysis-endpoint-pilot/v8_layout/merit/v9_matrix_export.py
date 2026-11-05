"""Download of one matrix (one analysis from one endpoint) from the frozen deposit, as a ZIP: matrix.tsv + manifest.json.

matrix.tsv has one row per position of the deposited matrix (a position is one sample row of a datatable or untarg_data
file, or one sample column of an mwTab data block): sample ID, factor label, ML-eligible yes/no, status, subject ID, then
one column per feature with the value exactly as deposited. Nothing is transformed, imputed or dropped.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

from merit import pilot_analysis_endpoint as pilot
from merit import structural_v9 as sv
from merit.manuscript_matrix_readers import _lines, _mwtab_layout

DUMP_ROOT = Path(os.environ.get("MERIT_MW_DUMP_ROOT", "/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version"))


def _values(path: Path, endpoint: str) -> tuple[list[str], list[list[str]]]:
    """(feature names, value cells per position) in the same positions and order as the roster."""
    if endpoint == "mwtab":
        columns, _samples, _labels, feature_rows = _mwtab_layout(path)
        return [r[0].strip() for r in feature_rows], [[r[k + 1].strip() if k + 1 < len(r) else "" for r in feature_rows] for k in columns]
    lines = _lines(path)
    while lines and not lines[0].strip():
        lines.pop(0)
    header = lines[0].split("\t")
    positions = [k for k in range(2, len(header)) if header[k].strip()]
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) > len(header) or not cells[0].strip():
            continue
        rows.append([cells[k] if k < len(cells) else "" for k in positions])
    return [header[k] for k in positions], rows


def _status(position: dict[str, Any]) -> str:
    row = position["row"]
    if row[sv.I_ALIGN]:
        return "Tier 1 sample alignment row (all zero, absent from the paired mwTab data block)"
    if position["excluded"]:
        return "left out by a sample edit"
    if position["eligible"]:
        return "ML-eligible" + (" (marked by a sample edit)" if row[sv.I_QC] else "")
    return "QC, blank or standard (not ML-eligible)" if row[sv.I_QC] else "not ML-eligible (marked by a sample edit)"


def matrix_zip(study_id: str, analysis_id: str, endpoint: str, params: dict | None = None, overrides: dict | None = None) -> tuple[bytes, str]:
    study_id, analysis_id = study_id.strip().upper(), analysis_id.strip().upper()
    raw = pilot._raw_study(study_id)
    ep = next((a["endpoints"].get(endpoint) for a in (raw or {}).get("analyses", []) if a["analysis_id"] == analysis_id), None)
    if not ep or "roster" not in ep:
        raise ValueError(f"No valid {endpoint} matrix for {study_id} / {analysis_id}.")
    path = DUMP_ROOT / ep["file"]
    features, values = _values(path, endpoint)
    roster = ep["roster"]
    if len(values) != len(roster) or any(len(v) != len(features) for v in values):
        raise ValueError("The deposited file does not match the stored sample roster.")
    positions = sv.effective_positions(roster, overrides)
    record = sv.compute(roster, params, overrides, ep.get("sample_table_lines"))
    out = io.StringIO()
    clean = lambda text: str(text).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    out.write("\t".join(["sample_id", "factor_label", "ml_eligible", "status", "subject_id"] + [clean(f) for f in features]) + "\n")
    for position, cells in zip(positions, values):
        row = position["row"]
        out.write("\t".join([clean(row[sv.I_ID]), clean(position["label"]), "yes" if position["eligible"] else "no", _status(position),
                             clean(row[sv.I_SUBJECT])] + [clean(c) for c in cells]) + "\n")
    manifest = {
        "schema": "merit.matrix_export.v1",
        "study_id": study_id, "analysis_id": analysis_id, "endpoint": endpoint,
        "source": "Metabolomics Workbench deposit",
        "source_file": ep["file"], "source_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "values": "exactly as deposited; nothing transformed, imputed, normalised or dropped",
        "rows": "one per position of the deposited matrix (one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block)",
        "features": len(features),
        "parameters": {k: float((params or {}).get(k, v)) for k, v in sv.DEFAULT_PARAMS.items()},
        "sample_edits": overrides or {},
        "structural": {k: record[k] for k in sv.ITEM_KEYS + ("structural", "positions", "tier1_alignment_rows", "excluded_positions", "measured_positions",
                                                           "qc_blank_positions", "eligible_positions", "distinct_eligible_ids", "value_cells", "numeric_cells",
                                                           "missing_entry_cells", "text_cells")},
    }
    buffer = io.BytesIO()
    name = f"{study_id}_{analysis_id}_{endpoint}"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}_matrix.tsv", out.getvalue())
        zf.writestr(f"{name}_manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
    return buffer.getvalue(), f"{name}_merit.zip"


def ml_ready_zip(study_id: str, analysis_id: str, endpoint: str, params: dict | None = None, overrides: dict | None = None) -> tuple[bytes, str]:
    """The ML-ready matrix of one analysis x endpoint, built when asked from the deposit files the page assessed (nothing stored):
    one row per ML-eligible position (QC-, blank- and standard-like positions, Tier 1 sample alignment rows and samples left out by a
    sample edit are not rows), with sample ID, subject ID, the class in force (the deposited whole factor string unless classes are
    chosen; empty when the sample is outside the chosen classes) and the deposited factor string, then one column per feature with the
    value exactly as deposited (decided by the author on 2026-11-01). Since 2026-11-01 (audit of Data at a glance): a position column (the
    sample's place in the deposited file: row of a datatable or untarg_data file, column of an mwTab data block, counted from 1), and the
    file is named for what it is (the matrix with the classes in force), not "ML-ready"."""
    import csv
    from merit import labels_v9 as lb, qc_blank_v9 as qb
    study_id, analysis_id = study_id.strip().upper(), analysis_id.strip().upper()
    raw = pilot._raw_study(study_id)
    ep = next((a["endpoints"].get(endpoint) for a in (raw or {}).get("analyses", []) if a["analysis_id"] == analysis_id), None)
    if not ep or "roster" not in ep:
        raise ValueError(f"No valid {endpoint} matrix for {study_id} / {analysis_id}.")
    path = DUMP_ROOT / ep["file"]
    features, values = _values(path, endpoint)
    roster = ep["roster"]
    if len(values) != len(roster) or any(len(v) != len(features) for v in values):
        raise ValueError("The deposited file does not match the stored sample roster.")
    overrides = overrides or {}
    kinds = qb.position_kinds(roster, overrides, study_id)
    choice, message = lb.parse_choice(params, lb.factor_items(raw, overrides))
    assigned = lb.sample_classes(roster, study_id, overrides, choice, ep.get("sample_table_conflicts"))
    out = io.StringIO(); w = csv.writer(out, lineterminator="\n")
    w.writerow(["sample_id", "position", "subject_id", "class", "factor_string"] + features)
    counts: dict = {}; rows = 0; outside = 0
    for pos, (row, kind, cells) in enumerate(zip(roster, kinds, values), start=1):
        if kind != "sample":
            continue
        sid = row[sv.I_ID]; cls = assigned.get(sid, ("", None))[0]
        subject = str((overrides.get(sid) or {}).get("subject", "") or "").strip() or row[sv.I_SUBJECT]   # as deposited unless edited
        w.writerow([sid, pos, subject, cls, lb.sample_label(row, overrides)] + cells)
        rows += 1; outside += not cls
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
    set_aside = {k: kinds.count(k) for k in ("qc", "blank", "standard", "set_aside", "alignment", "excluded") if kinds.count(k)}
    manifest = {
        "schema": "merit.matrix_classes_in_force.v2",
        "study_id": study_id, "analysis_id": analysis_id, "endpoint": endpoint,
        "source": "Metabolomics Workbench deposit (the files assessed on the page)", "built": "on request; nothing stored",
        "source_file": ep["file"], "source_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": "one per ML-eligible position (a sample row of a datatable or untarg_data file, or a sample column of an mwTab data block)",
        "position": "the sample's place in the deposited file, counted from 1 over every sample row (datatable, untarg_data) or sample column (mwTab), including positions not written",
        "values": "exactly as deposited: nothing transformed, imputed, normalised or dropped; empty cells stay empty and zeros stay zeros",
        "subject_id": "as deposited (for example '-' when nothing was entered), or the subject ID edited on the page",
        "class_definition": lb.choice_text(choice), "class_choice_message": message or None,
        "class_counts_by_row": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "rows_written": rows, "distinct_sample_ids_written": len({r[sv.I_ID] for r, k in zip(roster, kinds) if k == "sample"}),
        "rows_outside_the_classes": outside, "features": len(features),
        "positions_not_written": set_aside, "sample_edits": overrides,
        "note": "class counts here are rows; a sample ID measured at several positions gives several rows (the page counts each sample ID once)",
    }
    buffer = io.BytesIO(); name = f"{study_id}_{analysis_id}_{endpoint}_matrix_classes_in_force"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}.csv", out.getvalue())
        zf.writestr(f"{name}_manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
    return buffer.getvalue(), f"{name}.zip"
