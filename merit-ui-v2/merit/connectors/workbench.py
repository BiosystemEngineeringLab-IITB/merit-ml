from __future__ import annotations

import csv
import gzip
import io
import json
import re
from pathlib import Path
from typing import Any

from merit.models import (
    AssayRecord,
    CanonicalStudy,
    FeatureMatrix,
    MappingRecord,
    MetaboliteAnnotationRecord,
    ProvenanceRecord,
    SampleRecord,
    StudyRecord,
)
from merit.mw_archive import load_workbench_manifest, manifest_path as managed_manifest_path, resolve_mw_archive_root
from merit.remote import RemoteFetchError, download_gzip_text, download_text, fetch_json, fetch_text
from merit.serialization import compute_content_hash
from merit.feature_names import classify_feature_name, looks_like_lipid_structural_name, mzrt_lookup_key
from merit.utils import normalize_label, read_delimited_table, read_tsv_gz, safe_float, sha256_file, slugify
from merit.version import __version__

from .base import RepositoryConnector


def _set_csv_field_size_limit() -> None:
    """Raise csv parser field-size limit for very wide metabolomics rows.

    Some Workbench exports contain fields larger than the default 128 KiB cap.
    """
    limit = 1024 * 1024 * 1024  # 1 GiB hard cap for parser field size
    try:
        csv.field_size_limit(limit)
    except OverflowError:
        # Fallback for platforms with smaller C long.
        csv.field_size_limit(2**31 - 1)


_set_csv_field_size_limit()

# Column header names that indicate a file is features×samples (transposed layout)
_TRANSPOSED_ID_COLS = {
    "m/z_rt", "mz_rt", "mz/rt", "m/z", "mz",
    "mass_rt", "mass rt",
    "metabolite", "metabolite_name", "metabolite_names", "metabolite name", "metabolite_id",
    "name", "compound", "compound_name", "compound name", "compound_id", "compounds_id", "compounds id",
    "feature", "feature_id", "feature_name",
    "refmet_name", "hmdb_id", "pubchem_cid",
    # MW-specific _Results.txt identifiers
    "biochemical", "biochemical_name", "biochemical name",
    "chem_id", "chemical_id", "chemical_name",
    "metabolon_id", "refmet_super_pathway", "super_pathway",
    "kegg_id", "cas_id",
    # NMR binned headers
    "bin_range_ppm", "bin_range", "ppm_bin", "ppm_range", "chemical_shift_ppm",
}
_DUMP_ROOT_ALIASES = ("mw-dump-latest-confirmation", "mw-dump-latest", "mw_dump", "mw-dump")
_MWTAB_DATA_STARTS = {
    "MS_METABOLITE_DATA_START",
    "NMR_BINNED_DATA_START",
    "NMR_METABOLITE_DATA_START",
    "EXTENDED_MS_METABOLITE_DATA_START",
    "EXTENDED_NMR_METABOLITE_DATA_START",
    "DIRECT_INFUSION_METABOLITE_DATA_START",
    "METABOLITE_DATA_START",
}

_ROW_ORIENTED_SAMPLE_HEADERS = {"sample", "samples"}
_SAMPLE_SOURCE_KEYS = {
    "sample source",
    "sample_source",
    "tissue",
    "sample type",
    "sample_type",
    "matrix",
    "biofluid",
    "organism part",
    "organism_part",
    "cell type",
    "cell_type",
    "body fluid",
    "specimen type",
    "specimen",
}


class MetabolomicsWorkbenchConnector(RepositoryConnector):
    source_name = "workbench"
    connector_name = "MetabolomicsWorkbenchConnector"
    api_base = "https://www.metabolomicsworkbench.org/rest"

    def default_root(self, workspace: Path) -> Path:
        return resolve_mw_archive_root(workspace=workspace)

    def _candidate_roots(self, workspace: Path, root: str | None) -> list[Path]:
        if root:
            requested = Path(root)
            candidates = [requested]
            if requested.name in _DUMP_ROOT_ALIASES:
                candidates.extend(
                    requested.with_name(alias)
                    for alias in _DUMP_ROOT_ALIASES
                    if alias != requested.name
                )
            return list(dict.fromkeys(candidates))
        candidates = [workspace / alias for alias in _DUMP_ROOT_ALIASES]
        candidates.append(resolve_mw_archive_root(workspace=workspace))
        return list(dict.fromkeys(candidates))

    def _resolve_source_root(self, workspace: Path, root: str | None) -> Path:
        candidates = self._candidate_roots(workspace, root)
        for candidate in candidates:
            if (
                (candidate / "json").exists() or
                (candidate / "datatable").exists() or
                (candidate / "mwtab").exists() or
                (candidate / "studies").exists() or
                (candidate / "catalog.sqlite").exists() or
                any(candidate.glob("ST*/manifest.json"))
            ):
                return candidate
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _resolve_study_root(self, workspace: Path, root: str | None, study_id: str) -> Path:
        candidates = self._candidate_roots(workspace, root)
        for candidate in candidates:
            if self._latest_manifest_file(candidate, study_id).exists():
                return candidate
            if self._managed_manifest_file(candidate, study_id).exists():
                return candidate
            if self._resolve_json_path(candidate, study_id).exists():
                return candidate
        return self._resolve_source_root(workspace, root)

    @staticmethod
    def _json_candidates(source_root: Path, study_id: str) -> list[Path]:
        return [
            source_root / "json" / f"{study_id}.json",
            source_root / f"{study_id}.json",
            source_root / study_id / f"{study_id}.json",
        ]

    @staticmethod
    def _latest_manifest_file(source_root: Path, study_id: str) -> Path:
        return source_root / study_id / "manifest.json"

    def _resolve_json_path(self, source_root: Path, study_id: str) -> Path:
        for candidate in self._json_candidates(source_root, study_id):
            if candidate.exists():
                return candidate
        return self._json_candidates(source_root, study_id)[0]

    @staticmethod
    def _table_kind(path: Path) -> str:
        name = path.name.lower()
        if ".datatable.tsv" in name or "_datatable.tsv" in name:
            return "datatable"
        if (
            ".mwtab" in name
            or "_mwtab" in name
            or ("mwtab" in name and (name.endswith(".txt") or name.endswith(".txt.gz") or name.endswith(".mwtab")))
        ):
            return "mwtab"
        if "_untarg_data.tsv" in name or "_untarg_data.tsv.gz" in name:
            return "untarg"
        if "_results.txt" in name or name.endswith(".txt.gz") or name.endswith(".txt"):
            return "results"
        return "unknown"

    @classmethod
    def _is_table_path(cls, path: str | Path) -> bool:
        return cls._table_kind(Path(path)) in {"datatable", "mwtab", "untarg"}

    @classmethod
    def _table_priority(cls, path: Path) -> tuple[int, str]:
        return {
            "datatable": 0,
            "mwtab": 1,
            "untarg": 2,
            "unknown": 3,
        }.get(cls._table_kind(path), 3), path.name

    @staticmethod
    def _infer_analysis_id(path: Path, study_id: str) -> str:
        match = re.search(r"(AN\d+)", path.name, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        name = path.name.lower()
        if ".mwtab" in name or "_mwtab" in name or "mwtab" in name:
            return study_id.upper()
        return path.stem.upper()

    @staticmethod
    def _table_path_candidates(source_root: Path, study_id: str) -> list[Path]:
        candidates: list[Path] = []
        datatable_dir = source_root / "datatable" / study_id
        if datatable_dir.exists():
            for pattern in ("*_Results.txt", "*.datatable.tsv.gz", "*.mwtab", "*.mwtab.txt", "*mwtab*.txt"):
                candidates.extend(sorted(datatable_dir.glob(pattern)))
        latest_study_root = source_root / study_id
        if latest_study_root.exists():
            for analysis_dir in sorted(latest_study_root.glob("AN*")):
                tabular_dir = analysis_dir / "tabular"
                if tabular_dir.exists():
                    for pattern in ("*_Results.txt", "*_datatable.tsv", "*.datatable.tsv.gz", "*.tsv"):
                        candidates.extend(sorted(tabular_dir.glob(pattern)))
                json_dir = analysis_dir / "json"
                if json_dir.exists():
                    for pattern in ("*_mwtab.txt", "*.mwtab.txt", "*.mwtab"):
                        candidates.extend(sorted(json_dir.glob(pattern)))
        for pattern in (
            f"{study_id}*.mwtab",
            f"{study_id}*.mwtab.txt",
            f"{study_id}*mwtab*.txt",
        ):
            candidates.extend(sorted((source_root / "mwtab").glob(pattern)))
            candidates.extend(sorted((source_root / "mwtab" / study_id).glob(pattern)))
        return list(dict.fromkeys(candidate for candidate in candidates if candidate.is_file()))

    @staticmethod
    def _is_row_oriented_results(path: Path) -> bool:
        """Return True for row-oriented _Results.txt (first column = Sample/Samples in first rows)."""
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                for _ in range(5):
                    line = fh.readline()
                    if not line:
                        break
                    if line.split("\t")[0].strip().lower() in _ROW_ORIENTED_SAMPLE_HEADERS:
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def _read_results_txt(path: Path) -> list[dict[str, Any]]:
        """Parse row-oriented MW _Results.txt format.

        Layout: row with first-col='Sample'/'Samples' gives sample IDs, row with
        first-col='Factors' gives class labels, all other non-empty rows
        are feature rows (feature_name, value1, value2, ...).
        Returns rows in samples×features format compatible with normalize_bundle.
        """
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            raw_rows = [row for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE) if row]

        sample_ids: list[str] = []
        labels: list[str] = []
        feature_rows: list[tuple[str, list[str]]] = []

        for row in raw_rows:
            first = row[0].strip().lower()
            if first in _ROW_ORIENTED_SAMPLE_HEADERS:
                sample_ids = [s.strip() for s in row[1:]]
            elif first == "factors":
                labels = [v.strip() for v in row[1:]]
            elif row[0].strip():
                feature_rows.append((row[0].strip(), row[1:]))

        if not sample_ids:
            return []

        n = len(sample_ids)
        labels.extend([""] * (n - len(labels)))
        result: list[dict[str, Any]] = [
            {"_sample_id": sample_ids[i], "_class": labels[i]} for i in range(n)
        ]
        for feat_name, values in feature_rows:
            for i in range(n):
                result[i][feat_name] = values[i].strip() if i < len(values) else ""
        return result

    @staticmethod
    def _read_mwtab_table(path: Path) -> list[dict[str, str]]:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        in_section = False
        data_lines: list[str] = []
        for line in lines:
            token = line.strip()
            upper = token.upper()
            if not in_section:
                if upper in _MWTAB_DATA_STARTS:
                    in_section = True
                continue
            if upper.endswith("_END"):
                break
            if token:
                data_lines.append(line)
        if not data_lines:
            return []
        sample = "\n".join(data_lines[: min(5, len(data_lines))])
        delimiter = "\t"
        first_line = sample.splitlines()[0] if sample else ""
        tab_count = first_line.count("\t")
        comma_count = first_line.count(",")
        if sample:
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = "\t"
        if delimiter == "," and tab_count >= 2 and tab_count > comma_count:
            delimiter = "\t"
        # Workbench mwTab text can contain unmatched quotes in metabolite names.
        # Disable CSV quote semantics so quotes remain literal TSV content.
        reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter=delimiter, quoting=csv.QUOTE_NONE)
        return [dict(row) for row in reader]

    @staticmethod
    def _extract_mwtab_text_metadata(path: Path) -> dict[str, str]:
        """Extract lightweight metadata (data block + units) from mwtab text."""
        if not path.exists():
            return {"data_block": "", "units": "", "nmr_units": "", "ms_units": ""}
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except Exception:
            return {"data_block": "", "units": "", "nmr_units": "", "ms_units": ""}

        data_block = ""
        nmr_units = ""
        ms_units = ""
        generic_units = ""
        for line in lines:
            token = line.strip()
            upper = token.upper()
            if not data_block and upper in _MWTAB_DATA_STARTS:
                data_block = upper[:-6] if upper.endswith("_START") else upper
            if "\t" not in line:
                continue
            left, _, right = line.partition("\t")
            key = left.strip().upper()
            value = right.strip()
            if not value:
                continue
            if key in {"NMR_BINNED_DATA:UNITS", "NMR_METABOLITE_DATA:UNITS"} and not nmr_units:
                nmr_units = value
                continue
            if key in {"MS_METABOLITE_DATA:UNITS", "DIRECT_INFUSION_METABOLITE_DATA:UNITS"} and not ms_units:
                ms_units = value
                continue
            if key == "METABOLITE_DATA:UNITS" and not generic_units:
                generic_units = value
                continue

        units = ""
        if data_block.startswith("NMR_"):
            units = nmr_units or generic_units or ms_units
        else:
            units = ms_units or generic_units or nmr_units
        return {
            "data_block": data_block,
            "units": units,
            "nmr_units": nmr_units,
            "ms_units": ms_units,
        }

    def _read_table_rows(self, path: Path) -> list[dict[str, Any]]:
        if self._table_kind(path) == "mwtab":
            rows = self._read_mwtab_table(path)
            # mwtab data sections are row-oriented: first data line = "Samples\ts1\ts2..."
            # Many exported mwtab blocks begin with an empty first header cell:
            # "\tS0001\tS0002...". DictReader then exposes first key as "".
            first_key = str(list(rows[0].keys())[0] if rows else "").strip().lower()
            if rows and (first_key in _ROW_ORIENTED_SAMPLE_HEADERS or first_key == ""):
                return self._convert_row_oriented_rows(rows)
            return rows
        if self._table_kind(path) == "results" and self._is_row_oriented_results(path):
            return self._read_results_txt(path)
        return read_delimited_table(path)

    @staticmethod
    def _managed_manifest_file(source_root: Path, study_id: str) -> Path:
        return managed_manifest_path(source_root, study_id)

    @staticmethod
    def _to_factor_string(value: Any) -> str:
        if isinstance(value, dict):
            parts = []
            for key, item in value.items():
                key_text = str(key).strip()
                item_text = str(item).strip()
                if key_text and item_text:
                    parts.append(f"{key_text}:{item_text}")
            return "|".join(parts)
        return str(value or "").strip()

    @staticmethod
    def _split_unique_tokens(value: str) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()
        for token in re.split(r"[;/,]+", str(value or "")):
            normalized = token.strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            tokens.append(normalized)
        return tokens

    @staticmethod
    def _json_decode_with_trailing_object_recovery(text: str) -> Any:
        """Decode JSON, recovering from duplicated/truncated concatenated objects."""
        decoder = json.JSONDecoder()
        try:
            return decoder.decode(text)
        except json.JSONDecodeError as first_error:
            # Recovery mode A:
            # Accept a valid first object even if malformed/truncated payload is appended.
            try:
                payload, end_index = decoder.raw_decode(text)
                trailing = text[end_index:].strip()
                if trailing:
                    return payload
            except json.JSONDecodeError:
                pass

            # Recovery mode B:
            # Scan for a later fully valid object when a truncated object prefixes it.
            recovered_payload: Any | None = None
            for match in re.finditer(r"\{", text):
                candidate = text[match.start():]
                try:
                    payload, end_index = decoder.raw_decode(candidate)
                except json.JSONDecodeError:
                    continue
                if candidate[end_index:].strip():
                    continue
                recovered_payload = payload
            if recovered_payload is not None:
                return recovered_payload
            raise first_error

    @classmethod
    def _read_json_dict_with_recovery(cls, path: Path) -> dict[str, Any]:
        payload = cls._json_decode_with_trailing_object_recovery(
            path.read_text(encoding="utf-8", errors="replace")
        )
        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object in {path}")
        return payload

    @staticmethod
    def _resolve_data_format(data_format: str, subject_sample_factors: Any) -> str:
        """Return raw data format string.

        Prefers ANALYSIS.DATA_FORMAT.  When that is empty, falls back to file
        extensions extracted from RAW_FILE_NAME entries in SUBJECT_SAMPLE_FACTORS
        (e.g. '.mzML', '.d', '.raw').  If names exist but carry no extension,
        returns 'raw_files_linked' so downstream checks still register presence.
        """
        if data_format and data_format.strip():
            return data_format.strip()
        if not isinstance(subject_sample_factors, list):
            return ""
        raw_fnames: list[str] = []
        for row in subject_sample_factors:
            if not isinstance(row, dict):
                continue
            fname = (row.get("Additional sample data") or {}).get("RAW_FILE_NAME", "") or ""
            fname = fname.strip()
            if fname and fname != "Raw Data file":
                raw_fnames.append(fname)
        if not raw_fnames:
            return ""
        from pathlib import PurePosixPath
        exts = sorted({
            PurePosixPath(f).suffix.lower()
            for f in raw_fnames
            if PurePosixPath(f).suffix
        })
        return ", ".join(exts) if exts else "raw_files_linked"

    def _infer_analysis_type_from_payload(self, payload: dict[str, Any]) -> str:
        analysis_block = payload.get("ANALYSIS", {}) if isinstance(payload.get("ANALYSIS"), dict) else {}
        raw_type = str(analysis_block.get("ANALYSIS_TYPE", "") or "").strip()
        tokens = self._split_unique_tokens(raw_type)
        if tokens:
            return "; ".join(tokens)

        has_ms = any(
            key in payload
            for key in ("MS", "MS_METABOLITE_DATA", "DIRECT_INFUSION_METABOLITE_DATA")
        )
        has_nmr = any(
            key in payload
            for key in ("NM", "NMR", "NMR_METABOLITE_DATA", "NMR_BINNED_DATA")
        )
        inferred: list[str] = []
        if has_ms:
            inferred.append("MS")
        if has_nmr:
            inferred.append("NMR")
        return "; ".join(inferred)

    def _build_payload_from_latest_dump(
        self,
        study_id: str,
        analysis_json_paths: dict[str, Path],
        n_metabolites: dict[str, int | str],
        disease_payload: dict[str, Any] | None = None,
        factors_payload: dict[str, dict[str, str]] | None = None,
        metabolites_payload: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not analysis_json_paths:
            raise FileNotFoundError(f"No mwtab.json files found for {study_id}")

        analysis_payloads: dict[str, dict[str, Any]] = {}
        for analysis_id, json_path in analysis_json_paths.items():
            payload = self._read_json_dict_with_recovery(json_path)
            if isinstance(payload, dict):
                analysis_payloads[analysis_id] = payload

        if not analysis_payloads:
            raise ValueError(f"No readable mwtab.json payloads for {study_id}")

        first = next(iter(analysis_payloads.values()))
        subject = first.get("SUBJECT", {}) if isinstance(first.get("SUBJECT"), dict) else {}
        study_block = first.get("STUDY", {}) if isinstance(first.get("STUDY"), dict) else {}
        project_block = first.get("PROJECT", {}) if isinstance(first.get("PROJECT"), dict) else {}
        workbook_block = (
            first.get("METABOLOMICS WORKBENCH", {})
            if isinstance(first.get("METABOLOMICS WORKBENCH"), dict)
            else {}
        )
        submit_date = (
            self._first_non_empty(study_block, ("SUBMIT_DATE", "submit_date", "submission_date", "DATE_SUBMITTED"))
            or self._first_non_empty(project_block, ("SUBMIT_DATE", "submit_date", "submission_date", "DATE_SUBMITTED"))
            or self._first_non_empty(workbook_block, ("CREATED_ON", "created_on"))
        )
        release_date = (
            self._first_non_empty(study_block, ("RELEASE_DATE", "release_date", "PUBLIC_RELEASE_DATE", "date_released"))
            or self._first_non_empty(project_block, ("RELEASE_DATE", "release_date", "PUBLIC_RELEASE_DATE", "date_released"))
        )

        summary = {
            "study_id": study_id,
            "study_title": study_block.get("STUDY_TITLE", "") or project_block.get("PROJECT_TITLE", ""),
            "species": subject.get("SUBJECT_SPECIES", ""),
            "analysis_type": "",
            "release_date": release_date,
            "submit_date": submit_date,
            "institute": study_block.get("INSTITUTE", "") or project_block.get("INSTITUTE", ""),
        }
        analysis_types: list[str] = []
        for item in analysis_payloads.values():
            inferred_type = self._infer_analysis_type_from_payload(item)
            for token in self._split_unique_tokens(inferred_type):
                if token not in analysis_types:
                    analysis_types.append(token)
        summary["analysis_type"] = "; ".join(analysis_types)
        if factors_payload is None:
            factors_payload = self._fallback_factors_payload_from_analysis_payloads(analysis_payloads)

        analyses_payload: dict[str, dict[str, Any]] = {}
        for index, analysis_id in enumerate(sorted(analysis_payloads), start=1):
            payload = analysis_payloads[analysis_id]
            analysis_block = payload.get("ANALYSIS", {}) if isinstance(payload.get("ANALYSIS"), dict) else {}
            ms_block = payload.get("MS", {}) if isinstance(payload.get("MS"), dict) else {}
            nmr_block = payload.get("NM", {}) if isinstance(payload.get("NM"), dict) else {}
            if not nmr_block and isinstance(payload.get("NMR"), dict):
                nmr_block = payload.get("NMR", {})
            chromatography = payload.get("CHROMATOGRAPHY", {}) if isinstance(payload.get("CHROMATOGRAPHY"), dict) else {}
            ms_metabolite = payload.get("MS_METABOLITE_DATA", {}) if isinstance(payload.get("MS_METABOLITE_DATA"), dict) else {}
            nmr_metabolite = payload.get("NMR_METABOLITE_DATA", {}) if isinstance(payload.get("NMR_METABOLITE_DATA"), dict) else {}
            mwtab_text_meta: dict[str, str] = {}
            analysis_json_path = analysis_json_paths.get(analysis_id)
            if isinstance(analysis_json_path, Path):
                mwtab_text_path = analysis_json_path.with_name(f"{analysis_id}_mwtab.txt")
                mwtab_text_meta = self._extract_mwtab_text_metadata(mwtab_text_path)
            analysis_type_text = self._infer_analysis_type_from_payload(payload)
            nmr_experiment_type = nmr_block.get("NMR_EXPERIMENT_TYPE", "")
            nmr_instrument_type = nmr_block.get("INSTRUMENT_TYPE", "") or nmr_block.get("INSTRUMENT_NAME", "")
            nmr_reference = nmr_block.get("CHEMICAL_SHIFT_REF_CPD", "") or nmr_block.get("CHEMICAL_SHIFT_REF_STD", "")
            units = (
                ms_metabolite.get("Units", "")
                or nmr_metabolite.get("Units", "")
                or str((mwtab_text_meta or {}).get("units", "") or "")
            )
            data_block = str((mwtab_text_meta or {}).get("data_block", "") or "")
            analyses_payload[str(index)] = {
                "analysis_id": analysis_id,
                "analysis_type": analysis_type_text,
                "ion_mode": ms_block.get("ION_MODE", ""),
                "chromatography_type": chromatography.get("CHROMATOGRAPHY_TYPE", ""),
                "chromatography_system": chromatography.get("INSTRUMENT_NAME", ""),
                "chromatography_column": chromatography.get("COLUMN_NAME", ""),
                "units": units,
                "ms_type": ms_block.get("MS_TYPE", "") or nmr_block.get("NMR_EXPERIMENT_TYPE", ""),
                "ms_instrument_type": ms_block.get("INSTRUMENT_TYPE", "") or nmr_block.get("INSTRUMENT_TYPE", ""),
                "ms_instrument_name": ms_block.get("INSTRUMENT_NAME", "") or nmr_block.get("INSTRUMENT_NAME", ""),
                "nmr_experiment_type": nmr_experiment_type,
                "nmr_instrument_type": nmr_instrument_type,
                "nmr_spectrometer_frequency": nmr_block.get("SPECTROMETER_FREQUENCY", ""),
                "nmr_solvent": nmr_block.get("NMR_SOLVENT", ""),
                "nmr_pulse_sequence": nmr_block.get("PULSE_SEQUENCE", ""),
                "nmr_water_suppression": nmr_block.get("WATER_SUPPRESSION", ""),
                "nmr_reference_compound": nmr_reference,
                "nmr_temperature": nmr_block.get("TEMPERATURE", ""),
                "nmr_data_block": data_block,
                # Raw data format: prefer ANALYSIS.DATA_FORMAT; fall back to file
                # extensions extracted from RAW_FILE_NAME in SUBJECT_SAMPLE_FACTORS.
                "data_format": self._resolve_data_format(
                    analysis_block.get("DATA_FORMAT", ""),
                    payload.get("SUBJECT_SAMPLE_FACTORS"),
                ),
            }

        metabolite_rows = metabolites_payload or []
        if not metabolite_rows:
            metabolite_names: list[str] = []
            for payload in analysis_payloads.values():
                for block_key in ("MS_METABOLITE_DATA", "NMR_METABOLITE_DATA"):
                    block = payload.get(block_key, {}) if isinstance(payload.get(block_key), dict) else {}
                    data_rows = block.get("Data", []) if isinstance(block.get("Data"), list) else []
                    for row in data_rows:
                        if not isinstance(row, dict):
                            continue
                        name = str(row.get("Metabolite", "")).strip()
                        if name and name not in metabolite_names:
                            metabolite_names.append(name)
            metabolite_rows = [{"name": name} for name in metabolite_names]

        return {
            "input": study_id,
            "generated_by": "merit_latest_dump",
            "type": "STUDY",
            "summary": summary,
            "disease": disease_payload or {},
            "factors": factors_payload,
            "analyses": analyses_payload,
            "n_metabolites": n_metabolites,
            "metabolites": metabolite_rows,
            "project_summary": project_block.get("PROJECT_SUMMARY", "") or study_block.get("STUDY_SUMMARY", ""),
            "collection_summary": "",
            "sampleprep_summary": "",
            "project_block": project_block or None,
            "collection_block": first.get("COLLECTION"),
            "sampleprep_block": first.get("SAMPLEPREP"),
            "species": {
                "Study ID": study_id,
                "Latin name": subject.get("SUBJECT_SPECIES", ""),
                "Common name": subject.get("SUBJECT_SPECIES", ""),
            },
            "source": {"Study ID": study_id, "Sample source": ""},
        }

    @staticmethod
    def _first_non_empty(entry: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = entry.get(key)
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _normalize_factor_row(entry: dict[str, Any]) -> dict[str, str]:
        sample_id = MetabolomicsWorkbenchConnector._first_non_empty(
            entry,
            (
                "local_sample_id",
                "Sample ID",
                "sample_id",
                "Sample Name",
                "sample_name",
                "sample",
            ),
        )
        subject_id = MetabolomicsWorkbenchConnector._first_non_empty(
            entry,
            ("mb_sample_id", "Subject ID", "subject_id", "MB Sample ID", "mb_id"),
        )
        factors_value = entry.get("factors")
        if factors_value in (None, ""):
            factors_value = entry.get("Factors")
        factors_text = MetabolomicsWorkbenchConnector._to_factor_string(factors_value)
        sample_source = MetabolomicsWorkbenchConnector._first_non_empty(
            entry,
            (
                "sample_source",
                "Sample source",
                "sample type",
                "sample_type",
                "source",
                "Sample source type",
            ),
        )
        if not sample_source and isinstance(factors_value, dict):
            for key, value in factors_value.items():
                if str(key).strip().lower() in _SAMPLE_SOURCE_KEYS:
                    sample_source = str(value).strip()
                    break
        raw_data = MetabolomicsWorkbenchConnector._first_non_empty(
            entry,
            ("raw_data", "RAW_FILE_NAME", "raw_file_name", "Raw Data file"),
        )
        additional = entry.get("Additional sample data")
        if not raw_data and isinstance(additional, dict):
            raw_data = str(additional.get("RAW_FILE_NAME", "") or "").strip()
        if not sample_id and subject_id:
            sample_id = subject_id
        return {
            "study_id": MetabolomicsWorkbenchConnector._first_non_empty(entry, ("study_id", "Study ID")),
            "local_sample_id": sample_id,
            "sample_source": sample_source,
            "factors": factors_text,
            "mb_sample_id": subject_id,
            "raw_data": raw_data,
        }

    @classmethod
    def _factors_payload_from_rows(cls, rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
        payload: dict[str, dict[str, str]] = {}
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized = cls._normalize_factor_row(row)
            sample_id = normalized.get("local_sample_id", "")
            if not sample_id:
                continue
            if sample_id in seen:
                continue
            seen.add(sample_id)
            payload[str(len(payload) + 1)] = normalized
        return payload

    def _fallback_factors_payload_from_analysis_payloads(
        self,
        analysis_payloads: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, str]]:
        rows: list[dict[str, Any]] = []
        for payload in analysis_payloads.values():
            sample_factors = payload.get("SUBJECT_SAMPLE_FACTORS", [])
            if not isinstance(sample_factors, list):
                continue
            for entry in sample_factors:
                if isinstance(entry, dict):
                    rows.append(entry)
        return self._factors_payload_from_rows(rows)

    @staticmethod
    def _disease_cache_file(source_root: Path, study_id: str) -> Path:
        return source_root / study_id / "disease.json"

    @staticmethod
    def _factors_cache_file(source_root: Path, study_id: str) -> Path:
        return source_root / study_id / "factors.json"

    @staticmethod
    def _metabolites_cache_file(source_root: Path, study_id: str) -> Path:
        return source_root / study_id / "metabolites.json"

    def _resolve_latest_dump_disease_payload(
        self,
        source_root: Path,
        study_id: str,
        allow_remote_disease_fetch: bool,
    ) -> tuple[dict[str, Any], Path]:
        cache_path = self._disease_cache_file(source_root, study_id)
        if cache_path.exists():
            try:
                cached_payload = json.loads(cache_path.read_text())
                rows = self._as_rows(cached_payload)
                if rows:
                    return rows[0], cache_path
                if isinstance(cached_payload, dict):
                    return cached_payload, cache_path
            except Exception:
                pass
            return {}, cache_path

        if not allow_remote_disease_fetch:
            return {}, cache_path
        if not re.fullmatch(r"ST\d+", study_id.strip().upper()):
            return {}, cache_path
        try:
            remote_payload = fetch_json(
                self._study_endpoint(study_id, "disease"),
                timeout=10,
                retries=1,
                backoff_seconds=0.0,
            )
            rows = self._as_rows(remote_payload)
            disease_payload = rows[0] if rows else (remote_payload if isinstance(remote_payload, dict) else {})
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(disease_payload, sort_keys=True, indent=2, ensure_ascii=True))
            return disease_payload, cache_path
        except Exception:
            return {}, cache_path

    def _resolve_latest_dump_factors_payload(
        self,
        source_root: Path,
        study_id: str,
        analysis_json_paths: dict[str, Path],
        allow_remote_factors_fetch: bool,
    ) -> tuple[dict[str, dict[str, str]], Path]:
        cache_path = self._factors_cache_file(source_root, study_id)
        if cache_path.exists():
            try:
                cached_payload = json.loads(cache_path.read_text())
                rows = self._as_rows(cached_payload)
                factors_payload = self._factors_payload_from_rows(rows)
                if factors_payload:
                    return factors_payload, cache_path
            except Exception:
                pass
            return {}, cache_path

        if allow_remote_factors_fetch and re.fullmatch(r"ST\d+", study_id.strip().upper()):
            try:
                remote_payload = fetch_json(
                    self._study_endpoint(study_id, "factors"),
                    timeout=20,
                    retries=1,
                    backoff_seconds=0.0,
                )
                rows = self._as_rows(remote_payload)
                factors_payload = self._factors_payload_from_rows(rows)
                if factors_payload:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(list(factors_payload.values()), sort_keys=True, indent=2, ensure_ascii=True)
                    )
                    return factors_payload, cache_path
            except Exception:
                pass

        # Offline fallback: reuse factors from a legacy mw-dump JSON if available.
        for legacy_root in (
            source_root.with_name("mw-dump"),
            source_root.with_name("mw_dump"),
        ):
            legacy_json = legacy_root / "json" / f"{study_id}.json"
            if not legacy_json.exists():
                continue
            try:
                legacy_payload = json.loads(legacy_json.read_text())
            except Exception:
                continue
            rows = self._as_rows(legacy_payload.get("factors"))
            factors_payload = self._factors_payload_from_rows(rows)
            if factors_payload:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(list(factors_payload.values()), sort_keys=True, indent=2, ensure_ascii=True)
                )
                return factors_payload, cache_path

        analysis_payloads: dict[str, dict[str, Any]] = {}
        for analysis_id, json_path in analysis_json_paths.items():
            if not json_path.exists():
                continue
            try:
                payload = self._read_json_dict_with_recovery(json_path)
            except Exception:
                continue
            if isinstance(payload, dict):
                analysis_payloads[analysis_id] = payload
        factors_payload = self._fallback_factors_payload_from_analysis_payloads(analysis_payloads)
        if factors_payload:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(list(factors_payload.values()), sort_keys=True, indent=2, ensure_ascii=True)
            )
            return factors_payload, cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("[]\n", encoding="utf-8")
        return {}, cache_path

    def _resolve_latest_dump_metabolites_payload(
        self,
        source_root: Path,
        study_id: str,
        allow_remote_metabolites_fetch: bool,
    ) -> tuple[list[dict[str, Any]], Path]:
        cache_path = self._metabolites_cache_file(source_root, study_id)
        if cache_path.exists():
            try:
                cached_payload = json.loads(cache_path.read_text())
                rows = self._as_rows(cached_payload)
                return rows, cache_path
            except Exception:
                return [], cache_path

        if allow_remote_metabolites_fetch and re.fullmatch(r"ST\d+", study_id.strip().upper()):
            try:
                remote_payload = fetch_json(
                    self._study_endpoint(study_id, "metabolites"),
                    timeout=20,
                    retries=1,
                    backoff_seconds=0.0,
                )
                rows = self._as_rows(remote_payload)
                if rows:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(rows, sort_keys=True, indent=2, ensure_ascii=True))
                    return rows, cache_path
            except Exception:
                pass

        # Offline fallback: reuse metabolites from a legacy mw-dump JSON if available.
        for legacy_root in (
            source_root.with_name("mw-dump"),
            source_root.with_name("mw_dump"),
        ):
            legacy_json = legacy_root / "json" / f"{study_id}.json"
            if not legacy_json.exists():
                continue
            try:
                legacy_payload = json.loads(legacy_json.read_text())
                rows = self._as_rows(legacy_payload.get("metabolites"))
                if rows:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(rows, sort_keys=True, indent=2, ensure_ascii=True))
                    return rows, cache_path
            except Exception:
                continue

        # Last-resort fallback: derive per-analysis metabolite names from local mwtab JSON.
        rows = self._fallback_metabolites_rows_from_latest_dump(source_root, study_id)
        if rows:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(rows, sort_keys=True, indent=2, ensure_ascii=True))
            return rows, cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("[]\n", encoding="utf-8")
        return [], cache_path

    @staticmethod
    def _metabolite_name_from_data_row(row: dict[str, Any]) -> str:
        for key in (
            "Metabolite",
            "metabolite",
            "metabolite_name",
            "Metabolite name",
            "name",
            "Name",
            "Biochemical",
            "biochemical",
            "compound",
            "compound_name",
        ):
            value = row.get(key)
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text:
                    return text
        for _, value in row.items():
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text and safe_float(text) is None:
                    return text
        return ""

    def _fallback_metabolites_rows_from_latest_dump(
        self,
        source_root: Path,
        study_id: str,
    ) -> list[dict[str, Any]]:
        manifest_path = self._latest_manifest_file(source_root, study_id)
        if not manifest_path.exists():
            return []
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            return []
        analyses = manifest.get("analyses", {}) if isinstance(manifest, dict) else {}
        if not isinstance(analyses, dict) or not analyses:
            return []

        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for analysis_id, item in sorted(analyses.items()):
            if not isinstance(item, dict):
                continue
            rel_json = item.get("mwtab_json")
            if rel_json:
                mwtab_json = source_root / rel_json
            else:
                mwtab_json = source_root / study_id / str(analysis_id) / "json" / f"{analysis_id}_mwtab.json"
            if not mwtab_json.exists():
                continue
            try:
                payload = self._read_json_dict_with_recovery(mwtab_json)
            except Exception:
                continue
            analysis_summary = self._infer_analysis_type_from_payload(payload)
            for block_key in (
                "MS_METABOLITE_DATA",
                "NMR_METABOLITE_DATA",
                "NMR_BINNED_DATA",
                "DIRECT_INFUSION_METABOLITE_DATA",
                "METABOLITE_DATA",
            ):
                block = payload.get(block_key, {}) if isinstance(payload.get(block_key), dict) else {}
                data_rows = block.get("Data", []) if isinstance(block.get("Data"), list) else []
                for data_row in data_rows:
                    if not isinstance(data_row, dict):
                        continue
                    metabolite_name = self._metabolite_name_from_data_row(data_row)
                    if not metabolite_name:
                        continue
                    key = (str(analysis_id).strip().upper(), normalize_label(metabolite_name))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "study_id": study_id,
                            "analysis_id": str(analysis_id).strip().upper(),
                            "analysis_summary": analysis_summary,
                            "metabolite_name": metabolite_name,
                            "refmet_name": "",
                            "refmet_details": [],
                            "refmet_match_count": 0,
                            "regnos": [],
                            "compound_details": [],
                        }
                    )
            # Some legacy mwtab JSON payloads expose binned/NMR data in a top-level
            # "Data" list (identifier column like "Bin range(ppm)").
            top_data = payload.get("Data")
            if isinstance(top_data, list):
                for data_row in top_data:
                    if not isinstance(data_row, dict):
                        continue
                    metabolite_name = self._metabolite_name_from_data_row(data_row)
                    if not metabolite_name:
                        continue
                    key = (str(analysis_id).strip().upper(), normalize_label(metabolite_name))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "study_id": study_id,
                            "analysis_id": str(analysis_id).strip().upper(),
                            "analysis_summary": analysis_summary,
                            "metabolite_name": metabolite_name,
                            "refmet_name": "",
                            "refmet_details": [],
                            "refmet_match_count": 0,
                            "regnos": [],
                            "compound_details": [],
                        }
                    )
        return rows

    def _bundle_from_latest_dump(
        self,
        study_id: str,
        source_root: Path,
        allow_remote_disease_fetch: bool = True,
        allow_remote_factors_fetch: bool = True,
        allow_remote_metabolites_fetch: bool = True,
    ) -> dict[str, Any]:
        manifest_path = self._latest_manifest_file(source_root, study_id)
        if not manifest_path.exists():
            raise FileNotFoundError(f"Latest-dump manifest not found: {manifest_path}")
        manifest = json.loads(manifest_path.read_text())
        analyses = manifest.get("analyses", {}) if isinstance(manifest, dict) else {}
        if not isinstance(analyses, dict) or not analyses:
            raise ValueError(f"No analyses available in latest dump manifest: {manifest_path}")

        tabular_resolution: list[dict[str, Any]] = []
        datatable_files: list[str] = []      # primary chosen path per analysis (for n_metabolites)
        all_source_files: list[str] = []     # all available source paths (for file_manifest)
        analysis_json_paths: dict[str, Path] = {}
        n_metabolites: dict[str, int | str] = {}

        # Start from manifest-declared analyses, then add on-disk analysis folders
        # so we do not miss valid sources when manifest entries are partial/null.
        analysis_ids: set[str] = set(str(aid) for aid in analyses.keys())
        study_root = source_root / study_id
        if study_root.exists():
            for child in study_root.iterdir():
                if child.is_dir() and child.name.upper().startswith("AN"):
                    analysis_ids.add(child.name)

        for analysis_id in sorted(analysis_ids):
            item = analyses.get(analysis_id, {})
            if not isinstance(item, dict):
                item = {}
            candidate_rows: list[tuple[str, Path]] = []
            seen_candidate_paths: set[str] = set()

            def _add_candidate(kind: str, path: Path) -> None:
                key = str(path.resolve())
                if key in seen_candidate_paths:
                    return
                if path.exists() and path.is_file():
                    seen_candidate_paths.add(key)
                    candidate_rows.append((kind, path))

            # 1) Primary route: manifest-declared source paths.
            for key, kind in (("untarg_data", "untarg"), ("datatable", "datatable"), ("mwtab_txt", "mwtab")):
                rel = item.get(key)
                if not rel:
                    continue
                path = source_root / rel
                _add_candidate(kind, path)

            # 2) Fallback route: when manifest has null/missing source paths, discover
            # source files directly inside the analysis folder. This is intentionally
            # conservative (analysis-local patterns only) to avoid cross-analysis bleed.
            analysis_root = source_root / study_id / analysis_id
            tabular_dir = analysis_root / "tabular"
            json_dir = analysis_root / "json"

            if not any(kind == "untarg" for kind, _ in candidate_rows) and tabular_dir.exists():
                for pattern in (f"{analysis_id}_untarg_data.tsv", f"{analysis_id}_untarg_data.tsv.gz"):
                    for path in sorted(tabular_dir.glob(pattern)):
                        _add_candidate("untarg", path)

            if not any(kind == "datatable" for kind, _ in candidate_rows) and tabular_dir.exists():
                for pattern in (f"{analysis_id}_datatable.tsv", f"{analysis_id}_datatable.tsv.gz"):
                    for path in sorted(tabular_dir.glob(pattern)):
                        _add_candidate("datatable", path)

            if not any(kind == "mwtab" for kind, _ in candidate_rows):
                if json_dir.exists():
                    for pattern in (f"{analysis_id}_mwtab.txt", f"{analysis_id}_mwtab.txt.gz", f"{analysis_id}_mwtab.mwtab"):
                        for path in sorted(json_dir.glob(pattern)):
                            _add_candidate("mwtab", path)
                if tabular_dir.exists():
                    for pattern in (f"{analysis_id}_mwtab.txt", f"{analysis_id}_mwtab.txt.gz", f"{analysis_id}_mwtab.mwtab"):
                        for path in sorted(tabular_dir.glob(pattern)):
                            _add_candidate("mwtab", path)
            if not candidate_rows:
                continue

            ordered = sorted(candidate_rows, key=lambda pair: self._table_priority(pair[1]))
            # Primary selection: first candidate with detectable features
            chosen_kind, chosen_path = ordered[0]
            chosen_has_features = False
            chosen_feature_count = 0
            for kind, candidate in ordered:
                try:
                    rows = self._read_table_rows(candidate)
                    if rows and self._has_feature_columns(rows):
                        chosen_kind, chosen_path = kind, candidate
                        chosen_has_features = True
                        chosen_feature_count = self._estimate_feature_count(rows, candidate)
                        break
                except Exception:
                    continue
            datatable_files.append(str(chosen_path))
            if chosen_feature_count:
                n_metabolites[analysis_id] = chosen_feature_count
            else:
                n_metabolites[analysis_id] = ""
            # Emit one tabular_resolution entry per available source so trisource
            # assessment can filter independently via _source_filter.
            all_candidate_paths = [str(path) for _, path in ordered]
            for src_kind, src_path in ordered:
                all_source_files.append(str(src_path))
                # Check each candidate independently — primary result is reused;
                # non-primary sources are probed separately so mwtab files that
                # contain a real data block are not incorrectly marked as empty.
                if src_kind == chosen_kind:
                    src_has_features = chosen_has_features
                else:
                    try:
                        rows = self._read_table_rows(src_path)
                        src_has_features = bool(rows and self._has_feature_columns(rows))
                    except Exception:
                        src_has_features = False
                tabular_resolution.append(
                    {
                        "analysis_id": analysis_id,
                        "selected_path": str(src_path),
                        "selected_name": src_path.name,
                        "selected_kind": src_kind,
                        "selected_has_features": src_has_features,
                        "candidates": all_candidate_paths,
                        "is_primary": src_kind == chosen_kind,
                    }
                )

            rel_json = item.get("mwtab_json")
            if not rel_json:
                # Derive from mwtab_txt: replace .txt extension with .json
                rel_txt = item.get("mwtab_txt") or ""
                if rel_txt:
                    rel_json = rel_txt[:-4] + ".json" if rel_txt.endswith(".txt") else None
            if rel_json:
                json_path = source_root / rel_json
                if json_path.exists() and json_path.is_file():
                    analysis_json_paths[analysis_id] = json_path
            # Fallback: look for <AN_ID>_mwtab.json alongside mwtab_txt
            if analysis_id not in analysis_json_paths:
                rel_txt = item.get("mwtab_txt") or ""
                if rel_txt:
                    txt_path = source_root / rel_txt
                    candidate_json = txt_path.parent / (txt_path.stem + ".json")
                    if candidate_json.exists() and candidate_json.is_file():
                        analysis_json_paths[analysis_id] = candidate_json
            # Final fallback: infer mwtab json from analysis-local json folder.
            if analysis_id not in analysis_json_paths and json_dir.exists():
                for candidate_json in sorted(json_dir.glob(f"{analysis_id}_mwtab.json")):
                    if candidate_json.exists() and candidate_json.is_file():
                        analysis_json_paths[analysis_id] = candidate_json
                        break

        disease_payload, disease_cache_path = self._resolve_latest_dump_disease_payload(
            source_root,
            study_id,
            allow_remote_disease_fetch=allow_remote_disease_fetch,
        )
        factors_payload, factors_cache_path = self._resolve_latest_dump_factors_payload(
            source_root,
            study_id,
            analysis_json_paths,
            allow_remote_factors_fetch=allow_remote_factors_fetch,
        )
        metabolites_payload, metabolites_cache_path = self._resolve_latest_dump_metabolites_payload(
            source_root,
            study_id,
            allow_remote_metabolites_fetch=allow_remote_metabolites_fetch,
        )
        payload = self._build_payload_from_latest_dump(
            study_id,
            analysis_json_paths,
            n_metabolites,
            disease_payload=disease_payload,
            factors_payload=factors_payload,
            metabolites_payload=metabolites_payload,
        )
        combined_json_path = source_root / study_id / "__merit_combined.json"
        combined_json_path.write_text(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True))

        file_manifest = [str(manifest_path), str(combined_json_path)]
        if disease_cache_path.exists():
            file_manifest.append(str(disease_cache_path))
        if factors_cache_path.exists():
            file_manifest.append(str(factors_cache_path))
        if metabolites_cache_path.exists():
            file_manifest.append(str(metabolites_cache_path))
        file_manifest.extend(all_source_files)
        file_manifest.extend(str(path) for path in analysis_json_paths.values())

        return {
            "bundle_version": self.bundle_version,
            "source": self.source_name,
            "study_id": study_id,
            "source_root": str(source_root),
            "file_manifest": file_manifest,
            "json_path": str(combined_json_path),
            "analysis_json_paths": {analysis_id: str(path) for analysis_id, path in analysis_json_paths.items()},
            "tabular_resolution": tabular_resolution,
            "tabular_message": (
                f"Resolved {len(datatable_files)} tabular file(s) from latest MW dump layout."
                if datatable_files
                else "No tabular data found in latest MW dump layout."
            ),
            "acquisition_source": "latest_dump",
            "connector": self.connector_name,
            "parser_version": __version__,
            "fetched_remotely": False,
            "latest_manifest_path": str(manifest_path),
        }

    def _bundle_from_manifest(self, study_id: str, source_root: Path) -> dict[str, Any]:
        manifest = load_workbench_manifest(source_root, study_id)
        json_path = source_root / manifest["json"]["path"]
        selected_paths = [str(source_root / item["selected_path"]) for item in manifest.get("selected_assets", [])]
        tabular_resolution = []
        for item in manifest.get("selected_assets", []):
            tabular_resolution.append(
                {
                    "analysis_id": item.get("analysis_id", ""),
                    "selected_path": str(source_root / item.get("selected_path", "")),
                    "selected_name": item.get("selected_name", ""),
                    "selected_kind": item.get("selected_kind", ""),
                    "selected_has_features": item.get("selected_has_features", False),
                    "candidates": [str(source_root / candidate) for candidate in item.get("candidates", [])],
                }
            )
        return {
            "bundle_version": self.bundle_version,
            "source": self.source_name,
            "study_id": study_id,
            "source_root": str(source_root),
            "file_manifest": [str(json_path)] + selected_paths,
            "json_path": str(json_path),
            "tabular_resolution": tabular_resolution,
            "tabular_message": manifest.get("tabular_message", ""),
            "acquisition_source": "managed_archive",
            "connector": self.connector_name,
            "parser_version": __version__,
            "fetched_remotely": False,
        }

    def _pick_local_tables(
        self, source_root: Path, study_id: str, download_missing: bool = False
    ) -> tuple[list[Path], list[dict[str, Any]]]:
        grouped: dict[str, list[Path]] = {}
        for candidate in self._table_path_candidates(source_root, study_id):
            grouped.setdefault(self._infer_analysis_id(candidate, study_id), []).append(candidate)

        def select(group_items: dict[str, list[Path]]) -> tuple[list[Path], list[dict[str, Any]]]:
            selected_paths: list[Path] = []
            resolution: list[dict[str, Any]] = []
            for analysis_id in sorted(group_items):
                ordered = sorted(group_items[analysis_id], key=self._table_priority)
                chosen = ordered[0]
                chosen_has_features = False
                for candidate in ordered:
                    try:
                        rows = self._read_table_rows(candidate)
                        if self._has_feature_columns(rows):
                            chosen = candidate
                            chosen_has_features = True
                            break
                    except Exception:
                        continue
                # Results.txt is intentionally excluded as a source.
                selected_paths.append(chosen)
                resolution.append(
                    {
                        "analysis_id": analysis_id,
                        "selected_path": str(chosen),
                        "selected_kind": self._table_kind(chosen),
                        "selected_has_features": chosen_has_features,
                        "candidates": [str(path) for path in ordered],
                    }
                )
            return selected_paths, resolution

        analysis_specific = {
            analysis_id: candidates
            for analysis_id, candidates in grouped.items()
            if analysis_id.startswith("AN")
        }
        if analysis_specific:
            return select(analysis_specific)
        return select(grouped)

    def _bundle_from_paths(
        self,
        study_id: str,
        source_root: Path,
        fetched_remotely: bool = False,
        datatable_files: list[str] | None = None,
        tabular_resolution: list[dict[str, Any]] | None = None,
        download_missing: bool = False,
    ) -> dict[str, Any]:
        json_path = self._resolve_json_path(source_root, study_id)
        if datatable_files is None:
            resolved_files, tabular_resolution = self._pick_local_tables(
                source_root, study_id, download_missing=download_missing
            )
            datatable_files = [str(path) for path in resolved_files]
        tabular_resolution = tabular_resolution or []
        if not json_path.exists():
            raise FileNotFoundError(f"Metabolomics Workbench JSON not found: {json_path}")
        file_manifest = [str(json_path)] + datatable_files
        source_label = "remote fetch" if fetched_remotely else "local dump"
        return {
            "bundle_version": self.bundle_version,
            "source": self.source_name,
            "study_id": study_id,
            "source_root": str(source_root),
            "file_manifest": file_manifest,
            "json_path": str(json_path),
            "tabular_resolution": tabular_resolution,
            "tabular_message": (
                f"Resolved {len(datatable_files)} tabular file(s) from the {source_label}."
                if datatable_files
                else f"No tabular data found in {source_label}. Checked *_Results.txt, *.datatable.tsv.gz, and mwtab files."
            ),
            "acquisition_source": "remote_fetch" if fetched_remotely else "legacy_dump",
            "connector": self.connector_name,
            "parser_version": __version__,
            "fetched_remotely": fetched_remotely,
        }

    def _study_endpoint(self, study_id: str, output_item: str) -> str:
        return f"{self.api_base}/study/study_id/{study_id}/{output_item}"

    def _analysis_endpoint(self, analysis_id: str) -> str:
        return f"{self.api_base}/study/analysis_id/{analysis_id}/datatable/file"

    def _raw_download_page(self, study_id: str) -> str:
        return (
            "https://www.metabolomicsworkbench.org/data/DRCCStudySummary.php"
            f"?Mode=SetupRawDataDownload&StudyID={study_id}"
        )

    @staticmethod
    def _has_feature_columns(rows: list[dict[str, Any]]) -> bool:
        if not rows:
            return False
        first = rows[0]
        return len(first.keys()) > 2

    def _estimate_feature_count(self, rows: list[dict[str, Any]], table_path: Path) -> int:
        """Estimate feature count using the same transpose heuristic used during normalization."""
        if not rows:
            return 0
        columns = list(rows[0].keys())
        if len(columns) <= 2:
            return 0
        is_results_file = self._table_kind(table_path) == "results"
        if self._is_transposed(columns) or (is_results_file and len(rows) > len(columns)):
            pivoted = self._pivot_matrix(rows, columns)
            if not pivoted:
                return 0
            return max(0, len(pivoted[0].keys()) - 2)
        return max(0, len(columns) - 2)

    def _download_results_fallback(self, study_id: str, analysis_id: str, datatable_dir: Path) -> Path | None:
        html = fetch_text(self._raw_download_page(study_id))
        pattern = rf"{re.escape(study_id)}_{re.escape(analysis_id)}_Results\.txt"
        match = re.search(pattern, html)
        if not match:
            return None
        filename = match.group(0)
        url = f"https://www.metabolomicsworkbench.org/studydownload/{filename}"
        destination = datatable_dir / filename
        return download_text(url, destination)

    @staticmethod
    def _as_rows(payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            if payload and all(isinstance(value, dict) for value in payload.values()):
                return [value for value in payload.values() if isinstance(value, dict)]
            return [payload]
        return []

    def _remote_fetch(self, study_id: str, target_root: Path) -> dict[str, Any]:
        summary_rows = self._as_rows(fetch_json(self._study_endpoint(study_id, "summary")))
        if not summary_rows:
            raise RemoteFetchError(f"No summary rows returned for {study_id}")
        summary = summary_rows[0]
        disease_rows = self._as_rows(fetch_json(self._study_endpoint(study_id, "disease")))
        factors_rows = self._as_rows(fetch_json(self._study_endpoint(study_id, "factors")))
        analyses_rows = self._as_rows(fetch_json(self._study_endpoint(study_id, "analysis")))
        metabolites_rows = self._as_rows(fetch_json(self._study_endpoint(study_id, "metabolites")))

        source_root = target_root
        json_dir = source_root / "json"
        datatable_dir = source_root / "datatable" / study_id
        json_dir.mkdir(parents=True, exist_ok=True)
        datatable_dir.mkdir(parents=True, exist_ok=True)

        analyses_payload: dict[str, Any] = {}
        factors_payload: dict[str, Any] = {}
        n_metabolites: dict[str, Any] = {}
        datatable_files: list[str] = []
        selected_features: dict[str, bool] = {}

        for row in factors_rows:
            if not isinstance(row, dict):
                continue
            normalized = self._normalize_factor_row(row)
            if not normalized.get("local_sample_id"):
                continue
            factors_payload[str(len(factors_payload) + 1)] = normalized

        for index, row in enumerate(analyses_rows, start=1):
            analyses_payload[str(index)] = row
            analysis_id = str(
                row.get("analysis_id")
                or row.get("ANALYSIS_ID")
                or row.get("Analysis_ID")
                or row.get("Analysis ID")
                or ""
            ).strip()
            if not analysis_id:
                continue
            datatable_path = datatable_dir / f"{analysis_id}.datatable.tsv.gz"
            download_gzip_text(self._analysis_endpoint(analysis_id), datatable_path)
            selected_path: Path = datatable_path
            has_features = False
            try:
                rows = read_tsv_gz(datatable_path)
                has_features = self._has_feature_columns(rows)
                if not self._has_feature_columns(rows):
                    fallback_path = self._download_results_fallback(study_id, analysis_id, datatable_dir)
                    if fallback_path is not None:
                        fallback_rows = read_delimited_table(fallback_path)
                        if self._has_feature_columns(fallback_rows):
                            rows = fallback_rows
                            selected_path = fallback_path
                            has_features = True
                if rows:
                    n_metabolites[str(index)] = self._estimate_feature_count(rows, selected_path)
            except Exception:
                n_metabolites[str(index)] = ""
            datatable_files.append(str(selected_path))
            selected_features[analysis_id.upper()] = has_features

        combined_payload = {
            "input": study_id,
            "generated_by": "merit_remote_fetch",
            "type": "STUDY",
            "summary": summary,
            "disease": disease_rows[0] if disease_rows else {},
            "factors": factors_payload,
            "analyses": analyses_payload,
            "n_metabolites": n_metabolites,
            "metabolites": metabolites_rows,
            "project_summary": summary.get("study_title", ""),
            "collection_summary": "",
            "sampleprep_summary": "",
            "project_block": None,
            "collection_block": None,
            "sampleprep_block": None,
            "species": {"Study ID": study_id, "Latin name": summary.get("species", ""), "Common name": summary.get("species", "")},
            "source": {"Study ID": study_id, "Sample source": ""},
        }
        json_path = json_dir / f"{study_id}.json"
        json_path.write_text(json.dumps(combined_payload, sort_keys=True, indent=2, ensure_ascii=True))
        tabular_resolution = [
            {
                "analysis_id": self._infer_analysis_id(Path(path), study_id),
                "selected_path": path,
                "selected_kind": self._table_kind(Path(path)),
                "selected_has_features": selected_features.get(self._infer_analysis_id(Path(path), study_id), False),
                "candidates": [path],
            }
            for path in datatable_files
        ]
        return self._bundle_from_paths(
            study_id,
            source_root,
            fetched_remotely=True,
            datatable_files=datatable_files,
            tabular_resolution=tabular_resolution,
        )

    def create_bundle(
        self,
        study_id: str,
        workspace: Path,
        root: str | None = None,
        fetch_mode: str = "auto",
        download_root: str | None = None,
    ) -> dict[str, Any]:
        source_root = self._resolve_study_root(workspace, root, study_id)
        latest_manifest = self._latest_manifest_file(source_root, study_id)
        managed_manifest = self._managed_manifest_file(source_root, study_id)
        json_path = self._resolve_json_path(source_root, study_id)
        if fetch_mode not in {"auto", "local", "remote"}:
            raise ValueError(f"Unsupported fetch mode: {fetch_mode}")
        if fetch_mode in {"auto", "local"} and latest_manifest.exists():
            return self._bundle_from_latest_dump(
                study_id,
                source_root,
                allow_remote_disease_fetch=True,
                allow_remote_factors_fetch=True,
            )
        if fetch_mode in {"auto", "local"} and managed_manifest.exists():
            return self._bundle_from_manifest(study_id, source_root)
        if fetch_mode in {"auto", "local"} and json_path.exists():
            return self._bundle_from_paths(
                study_id, source_root, fetched_remotely=False,
                download_missing=(fetch_mode == "auto"),
            )
        if fetch_mode == "local":
            raise FileNotFoundError(
                f"Metabolomics Workbench study not found in latest dump, managed archive, or legacy dump: "
                f"{latest_manifest} / {managed_manifest} / {json_path}"
            )
        target_root = Path(download_root) if download_root else source_root
        try:
            return self._remote_fetch(study_id, target_root)
        except RemoteFetchError:
            if fetch_mode == "auto" and latest_manifest.exists():
                return self._bundle_from_latest_dump(study_id, source_root)
            if fetch_mode == "auto" and managed_manifest.exists():
                return self._bundle_from_manifest(study_id, source_root)
            if fetch_mode == "auto" and json_path.exists():
                return self._bundle_from_paths(study_id, source_root, fetched_remotely=False)
            raise

    def _parse_factors(self, payload: dict[str, Any]) -> dict[str, dict[str, str]]:
        factors = payload.get("factors", {}) or {}
        result: dict[str, dict[str, str]] = {}
        if isinstance(factors, dict):
            rows = [item for item in factors.values() if isinstance(item, dict)]
        elif isinstance(factors, list):
            rows = [item for item in factors if isinstance(item, dict)]
        else:
            rows = []
        for item in rows:
            normalized = self._normalize_factor_row(item)
            sample_id = (normalized.get("local_sample_id") or "").strip()
            if sample_id and not self._is_artifact_sample_row(sample_id, str(normalized.get("factors", ""))):
                result[sample_id] = normalized
        return result

    @staticmethod
    def _is_transposed(columns: list[str]) -> bool:
        """Return True if the table is features×samples (first column is a feature ID header)."""
        if not columns:
            return False
        first_raw = columns[0].strip().lower()
        first_space_norm = first_raw.replace(" ", "_")
        first_token_norm = re.sub(r"[^a-z0-9]+", "_", first_raw).strip("_")
        if (
            first_raw in _TRANSPOSED_ID_COLS
            or first_space_norm in _TRANSPOSED_ID_COLS
            or first_token_norm in _TRANSPOSED_ID_COLS
        ):
            return True
        # Common NMR binned header shape: "Bin range(ppm)"
        if "bin" in first_raw and "ppm" in first_raw:
            return True
        return False

    @staticmethod
    def _pivot_matrix(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
        """Pivot a features×samples table to samples×features format.

        columns[0] is the feature-ID column; columns[1:] are sample IDs.
        If a row has feature name 'Factors', its values are used as class labels.
        Returns rows in samples×features format with '_sample_id' and '_class' as
        the first two columns (Python dict ordering preserved).
        """
        sample_ids = columns[1:]
        factors: dict[str, str] = {}
        feature_rows = []
        for row in rows:
            feat_name = str(row.get(columns[0]) or "").strip()
            if feat_name.lower() == "factors":
                factors = {sid: str(row.get(sid, "")).strip() for sid in sample_ids}
            elif feat_name:
                feature_rows.append(row)
        sample_rows: list[dict[str, Any]] = [
            {"_sample_id": sid, "_class": factors.get(sid, "")} for sid in sample_ids
        ]
        for row in feature_rows:
            feat_name = str(row.get(columns[0]) or "").strip()
            if not feat_name:
                continue
            for i, sid in enumerate(sample_ids):
                sample_rows[i][feat_name] = row.get(sid, "")
        return sample_rows

    @staticmethod
    def _convert_row_oriented_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert DictReader output from row-oriented mwtab/datatable to samples×features.

        In row-oriented format the DictReader first line became column headers, so
        the column keys after the first are the sample IDs.  Row values whose
        first-column value is 'Factors' supply class labels; all other non-empty
        rows are feature rows.
        """
        if not rows:
            return []
        all_keys = list(rows[0].keys())
        first_col = all_keys[0]
        # Strip any empty-string or whitespace-only keys — these arise from trailing
        # tab characters on the header line of mwtab data sections.
        sample_ids = [k for k in all_keys[1:] if str(k).strip()]
        if not sample_ids:
            return []
        n = len(sample_ids)
        labels = [""] * n
        feature_data: list[tuple[str, list[str]]] = []
        for row in rows:
            row_label = str(row.get(first_col) or "").strip()
            if row_label.lower() == "factors":
                labels = [str(row.get(sid, "")).strip() for sid in sample_ids]
            elif row_label and row_label.lower() not in _ROW_ORIENTED_SAMPLE_HEADERS:
                feature_data.append(
                    (row_label, [str(row.get(sid, "")).strip() for sid in sample_ids])
                )
        result: list[dict[str, Any]] = [
            {"_sample_id": sample_ids[i], "_class": labels[i]} for i in range(n)
        ]
        for feat_name, values in feature_data:
            for i in range(n):
                result[i][feat_name] = values[i] if i < len(values) else ""
        return result

    @staticmethod
    def _select_label_key(factor_lookup: dict[str, dict[str, str]]) -> str:
        """Identify the primary class-label factor key.

        Purely data-driven — no hardcoded domain tokens or type filters.
        Factor strings use '|' as the only inter-pair delimiter (confirmed
        across the full MW corpus; ';' and ',' appear only inside values).

        Ranking:
        1. Keys with ≥2 distinct values (constant keys are useless as labels).
        2. Fewest distinct values — the key that partitions samples into the
           smallest number of groups is the simplest ML endpoint.
        3. Highest sample coverage as tiebreaker.
        4. Key name alphabetically for determinism.
        """
        from collections import Counter as _Counter, defaultdict as _defaultdict

        key_coverage: _Counter[str] = _Counter()
        key_values: dict[str, set[str]] = _defaultdict(set)
        for sample_data in factor_lookup.values():
            factors_text = sample_data.get("factors", "")
            for part in factors_text.split("|"):
                part = part.strip()
                if ":" in part:
                    k, _, v = part.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k and v:
                        key_coverage[k] += 1
                        key_values[k].add(v)
        if not key_coverage:
            return ""

        def _rank(key: str) -> tuple[int, int, int, str]:
            distinct = len(key_values.get(key, set()))
            insufficient = 0 if distinct >= 2 else 1
            return (
                insufficient,
                distinct,                   # fewest groups preferred
                -key_coverage.get(key, 0),  # higher coverage preferred
                key.casefold(),
            )

        return min(key_coverage.keys(), key=_rank)

    @staticmethod
    def _value_for_key(factors_text: str, key: str) -> str:
        """Extract value for a specific key from a pipe-delimited factor string."""
        if not factors_text or not key:
            return ""
        for part in factors_text.split("|"):
            part = part.strip()
            if ":" in part:
                k, _, v = part.partition(":")
                if k.strip().lower() == key.lower() and v.strip():
                    return v.strip()
        return ""

    @staticmethod
    def _extract_primary_label(factors_text: str) -> str:
        """Extract primary biological class label from a MW factor string.

        Factor strings are pipe-delimited 'Key:Value' pairs such as
        'Diagnosis:cancer | Treatment:drug', or plain values like 'cancer'.
        Returns the first non-empty *value* portion. Filters out values that
        look like factor variable name artifacts (e.g. 'factor1_classification').
        """
        if not factors_text:
            return ""
        _HEADER_LIKE = re.compile(r"^factor\d+[_\s]\w+$", re.IGNORECASE)
        for part in factors_text.split("|"):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                value = part.split(":", 1)[1].strip()
                if value and not _HEADER_LIKE.match(value):
                    return value
            elif not _HEADER_LIKE.match(part):
                return part
        return ""

    @staticmethod
    def _compose_class_label(factors_text: str) -> str:
        """Return the full factor string as a single class label.

        Preserves all factor dimensions in-order and normalizes only spacing
        around delimiters. Example:
        "Hours:8|Compactin:None|KLA:None" ->
        "Hours:8 | Compactin:None | KLA:None"
        """
        def _normalize_factor_value(key: str, value: str) -> str:
            key_norm = re.sub(r"[\s_]+", "", str(key or "").strip().casefold())
            val_raw = str(value or "").strip()
            val_norm = val_raw.casefold()
            if key_norm in {"compactin", "kla"}:
                if val_norm in {"", "0", "0.0", "none", "na", "n/a", "null", "-"}:
                    return "None"
                if key_norm == "compactin" and val_norm in {"1", "1.0", "50um", "50uM".casefold()}:
                    return "50uM"
                if key_norm == "kla" and val_norm in {"1", "1.0", "100ng/ml"}:
                    return "100ng/ml"
            return val_raw

        text = str(factors_text or "").strip()
        if not text:
            return ""
        # Also normalize a single key:value string (no pipe present).
        parts = [part.strip() for part in text.split("|") if part.strip()]
        normalized_parts: list[str] = []
        for part in parts:
            if ":" in part:
                key, _, value = part.partition(":")
                key = key.strip()
                value = _normalize_factor_value(key, value)
                if key and value:
                    normalized_parts.append(f"{key}:{value}")
                elif key:
                    normalized_parts.append(f"{key}:")
                elif value:
                    normalized_parts.append(value)
            else:
                normalized_parts.append(part)
        return " | ".join(normalized_parts) if "|" in text else (normalized_parts[0] if normalized_parts else "")

    @staticmethod
    def _is_artifact_sample_row(sample_id: str, raw_label: str) -> bool:
        """Detect in-band header/annotation rows emitted as fake samples.

        Some MW datatable exports include a pseudo-row such as:
        Samples='Sample name', Class='Factor1:Classification', all-zero features.
        This must not be promoted to a real sample.
        """
        sid = " ".join((sample_id or "").strip().split()).lower()
        if sid in {"sample", "sample name", "sample_name", "sample id", "sample_id", "samples"}:
            return True
        if sid.startswith("sample ") and sid.endswith("name"):
            return True
        label = (raw_label or "").strip().lower()
        if sid in {"sample", "classification"} and ("class" in label or "factor" in label):
            return True
        return False

    @staticmethod
    def _load_refmet_by_name(source_root: Path) -> dict[str, dict[str, str]]:
        """Load the refmet-by-name lookup dict from mw-dump if available."""
        import gzip as _gzip
        candidates = [
            source_root / "refmet_by_name_dict.json.gz",
            source_root.parent / "mw-dump" / "refmet_by_name_dict.json.gz",
            source_root.parent / "mw_dump" / "refmet_by_name_dict.json.gz",
            source_root.parent / "mw-dump-latest" / "refmet_by_name_dict.json.gz",
            source_root.parent / "mw_dump-latest" / "refmet_by_name_dict.json.gz",
        ]
        seen: set[Path] = set()
        for path in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if not path.exists():
                continue
            try:
                with _gzip.open(path) as fh:
                    payload = json.loads(fh.read())
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
        return {}

    @staticmethod
    def _refmet_lookup_entry(refmet_by_name: dict[str, dict[str, str]], name: Any) -> dict[str, Any] | None:
        return MetabolomicsWorkbenchConnector._refmet_lookup_entry_with_index(refmet_by_name, name, {})

    @staticmethod
    def _refmet_lookup_entry_with_index(
        refmet_by_name: dict[str, dict[str, str]],
        name: Any,
        refmet_name_index: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        text = str(name or "").strip()
        if not text:
            return None
        direct = refmet_by_name.get(text)
        if isinstance(direct, dict):
            return direct
        normalized = normalize_label(text)
        norm_match = refmet_by_name.get(normalized)
        if isinstance(norm_match, dict):
            return norm_match
        if refmet_name_index:
            if normalized and normalized != "unknown":
                indexed = refmet_name_index.get(normalized)
                if isinstance(indexed, dict):
                    return indexed
            compact = re.sub(r"[^a-z0-9]+", "", text.lower())
            if compact:
                indexed = refmet_name_index.get(compact)
                if isinstance(indexed, dict):
                    return indexed
        return None

    @staticmethod
    def _refmet_info_rank(info: dict[str, Any]) -> int:
        score = 0
        if not isinstance(info, dict):
            return score
        if str(info.get("super_class", "") or "").strip():
            score += 4
        if str(info.get("main_class", "") or "").strip():
            score += 2
        if str(info.get("sub_class", "") or "").strip():
            score += 1
        if str(info.get("refmet_id", "") or "").strip():
            score += 2
        if str(info.get("formula", "") or "").strip():
            score += 1
        if str(info.get("inchi_key", "") or "").strip():
            score += 1
        return score

    @classmethod
    def _build_refmet_name_index(cls, refmet_by_name: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        if not isinstance(refmet_by_name, dict):
            return index

        def upsert(token: str, payload: dict[str, Any]) -> None:
            token = token.strip()
            if not token or token == "unknown":
                return
            existing = index.get(token)
            if existing is None or cls._refmet_info_rank(payload) > cls._refmet_info_rank(existing):
                index[token] = payload

        for key, payload in refmet_by_name.items():
            if not isinstance(payload, dict):
                continue
            candidates = [str(key or "").strip(), str(payload.get("name", "") or "").strip()]
            for candidate in candidates:
                if not candidate:
                    continue
                normalized = normalize_label(candidate)
                compact = re.sub(r"[^a-z0-9]+", "", candidate.lower())
                if normalized and normalized != "unknown":
                    upsert(normalized, payload)
                if compact:
                    upsert(compact, payload)
        return index

    @staticmethod
    def _metabolite_row_name(row: dict[str, Any]) -> str:
        for key in (
            "metabolite_name",
            "Metabolite",
            "metabolite",
            "name",
            "compound_name",
            "compound",
            "refmet_name",
        ):
            value = row.get(key)
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _metabolite_row_aliases(row: dict[str, Any]) -> list[str]:
        aliases: list[str] = []
        seen: set[str] = set()
        for key in (
            "metabolite_name",
            "Metabolite",
            "metabolite",
            "name",
            "compound_name",
            "compound",
            "refmet_name",
        ):
            value = row.get(key)
            if not isinstance(value, (str, int, float)):
                continue
            text = str(value).strip()
            if not text:
                continue
            normalized = normalize_label(text)
            if not normalized or normalized == "unknown" or normalized in seen:
                continue
            seen.add(normalized)
            aliases.append(normalized)
            mzrt_key = mzrt_lookup_key(text)
            if mzrt_key and mzrt_key not in seen:
                seen.add(mzrt_key)
                aliases.append(mzrt_key)
        return aliases

    @staticmethod
    def _metabolite_row_rank(row: dict[str, Any]) -> int:
        score = 0
        if str(row.get("refmet_name", "") or "").strip():
            score += 3
        refmet_details = row.get("refmet_details")
        if isinstance(refmet_details, list) and refmet_details and isinstance(refmet_details[0], dict):
            score += 5
        compound_details = row.get("compound_details")
        if isinstance(compound_details, list) and compound_details and isinstance(compound_details[0], dict):
            score += 2
        try:
            score += min(int(row.get("refmet_match_count") or 0), 3)
        except (TypeError, ValueError):
            pass
        return score

    @classmethod
    def _metabolite_row_super_class(
        cls,
        row: dict[str, Any],
        refmet_by_name: dict[str, dict[str, str]],
        refmet_name_index: dict[str, dict[str, Any]],
    ) -> str:
        if not isinstance(row, dict):
            return ""
        # 1. refmet_details embedded in the row (legacy enriched format)
        refmet_details = row.get("refmet_details")
        if isinstance(refmet_details, list) and refmet_details and isinstance(refmet_details[0], dict):
            super_class = str(refmet_details[0].get("super_class", "") or "").strip()
            if super_class:
                return super_class
        # 2. Direct super_class field (added by enrich_metabolites_refmet.py for
        #    metabolites that already had refmet_name in the source data)
        direct_sc = str(row.get("super_class", "") or "").strip()
        if direct_sc:
            return direct_sc
        return ""

    @staticmethod
    def _metabolite_row_analysis_id(row: dict[str, Any]) -> str:
        for key in ("analysis_id", "ANALYSIS_ID", "Analysis_ID", "Analysis ID"):
            value = row.get(key)
            if isinstance(value, (str, int, float)):
                text = str(value).strip().upper()
                if text:
                    return text
        return ""

    @staticmethod
    def _build_metabolite_lookup(
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]], list[str]]:
        by_analysis: dict[str, dict[str, dict[str, Any]]] = {}
        global_lookup: dict[str, dict[str, Any]] = {}
        names: list[str] = []

        def upsert(target: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
            existing = target.get(key)
            if existing is None or MetabolomicsWorkbenchConnector._metabolite_row_rank(row) > MetabolomicsWorkbenchConnector._metabolite_row_rank(existing):
                target[key] = row

        for row in rows:
            if not isinstance(row, dict):
                continue
            aliases = MetabolomicsWorkbenchConnector._metabolite_row_aliases(row)
            if not aliases:
                continue
            analysis_id = MetabolomicsWorkbenchConnector._metabolite_row_analysis_id(row)
            if analysis_id:
                analysis_lookup = by_analysis.setdefault(analysis_id, {})
                for alias in aliases:
                    upsert(analysis_lookup, alias, row)
            for alias in aliases:
                upsert(global_lookup, alias, row)

            raw_name = MetabolomicsWorkbenchConnector._metabolite_row_name(row)
            refmet_name = str(row.get("refmet_name", "") or "").strip()
            for candidate in (raw_name, refmet_name):
                if candidate and candidate not in names:
                    names.append(candidate)
        return by_analysis, global_lookup, names

    @staticmethod
    def _extract_disease_name(disease_payload: Any) -> str:
        if not isinstance(disease_payload, dict):
            return ""
        missing_tokens = {"", "na", "n/a", "none", "null", "-", "unknown"}

        # MW study disease is sourced from STxxxx/disease.json.
        for key in ("Disease", "disease", "Disease Name", "disease_name"):
            value = disease_payload.get(key)
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text and text.casefold() not in missing_tokens:
                    return text

        # Optional cache helper: fan-out files may also carry a normalized list.
        terms = disease_payload.get("disease_terms")
        if isinstance(terms, list):
            for value in terms:
                text = str(value).strip()
                if text and text.casefold() not in missing_tokens:
                    return text
        return ""

    def normalize_bundle(self, bundle: dict[str, Any]) -> CanonicalStudy:
        json_path = Path(bundle["json_path"])
        opener = gzip.open if json_path.suffix == ".gz" else open
        with opener(json_path, "rt", encoding="utf-8") as handle:
            payload = json.loads(handle.read())
        json_resolved = json_path.resolve()
        excluded_paths = {json_resolved}
        latest_manifest_path = bundle.get("latest_manifest_path")
        if latest_manifest_path:
            excluded_paths.add(Path(latest_manifest_path).resolve())
        for path in (bundle.get("analysis_json_paths") or {}).values():
            excluded_paths.add(Path(path).resolve())
        datatable_files = []
        for path in bundle.get("file_manifest", []):
            resolved = Path(path).resolve()
            if resolved in excluded_paths:
                continue
            if not self._is_table_path(path):
                continue
            datatable_files.append(Path(path))
        resolution_by_path = {
            str(Path(item.get("selected_path", "")).resolve()): item
            for item in bundle.get("tabular_resolution", [])
            if item.get("selected_path")
        }
        # Optional source filter: only load files whose resolved kind is in the set.
        # Passed as bundle["_source_filter"] = {"datatable"} | {"mwtab"} | {"results"}.
        # None/empty means load all sources (default behaviour).
        _source_filter: set[str] | None = None
        _raw_filter = bundle.get("_source_filter")
        if _raw_filter:
            _source_filter = {str(k).lower() for k in _raw_filter}
        if _source_filter:
            datatable_files = [
                p for p in datatable_files
                if resolution_by_path.get(str(p.resolve()), {}).get("selected_kind", "").lower()
                in _source_filter
            ]
        factor_lookup = self._parse_factors(payload)
        label_key = self._select_label_key(factor_lookup)

        summary = payload.get("summary", {})
        disease_payload = payload.get("disease", {}) or {}

        submission_date = (
            summary.get("submit_date") or summary.get("study_submit_date") or
            summary.get("submission_date") or summary.get("date_submitted") or ""
        )
        release_date = (
            summary.get("release_date") or summary.get("study_release_date") or
            summary.get("public_release_date") or summary.get("date_released") or ""
        )
        study = StudyRecord(
            study_id=bundle["study_id"],
            title=summary.get("study_title", ""),
            description=payload.get("project_summary", "") or payload.get("collection_summary", ""),
            organism=summary.get("species", ""),
            disease=self._extract_disease_name(disease_payload),
            repository=self.source_name,
            analysis_type=summary.get("analysis_type", ""),
            platform=summary.get("analysis_type", ""),
            publication_date=release_date,
            raw_fields={
                "institute": summary.get("institute", ""),
                "number_of_samples": summary.get("number_of_samples", ""),
                "analyses": list((payload.get("analyses") or {}).keys()),
                "submission_date": submission_date,
                "release_date": release_date,
                # FAIR provenance fields extracted from PROJECT block
                "doi": str((_pb := payload.get("project_block") or {}).get("DOI", "") or "").strip(),
                "publications": str(_pb.get("PUBLICATIONS", "") or "").strip(),
                "funding_source": str(_pb.get("FUNDING_SOURCE", "") or "").strip(),
                "contributors": str(_pb.get("CONTRIBUTORS", "") or "").strip(),
                "project_type": str(_pb.get("PROJECT_TYPE", "") or "").strip(),
            },
        )

        # Build sample pool from the active tabular source rows only.
        # Do not pre-seed from global factors metadata because datatable/mwtab
        # can legitimately use different sample-ID namespaces in the same study.
        samples: list[SampleRecord] = []
        sample_lookup: dict[str, SampleRecord] = {}

        analyses = payload.get("analyses", {}) or {}
        n_metabolites = payload.get("n_metabolites", {}) or {}
        analysis_by_id: dict[str, dict[str, Any]] = {}
        for analysis in analyses.values():
            if not isinstance(analysis, dict):
                continue
            analysis_id = str(analysis.get("analysis_id") or "").strip().upper()
            if analysis_id:
                analysis_by_id[analysis_id] = analysis
        assays: list[AssayRecord] = []
        feature_matrices: list[FeatureMatrix] = []
        annotations: list[MetaboliteAnnotationRecord] = []
        mappings: list[MappingRecord] = []

        # Load RefMet name lookup for chemical class annotations
        refmet_by_name = self._load_refmet_by_name(Path(bundle["source_root"]))
        refmet_name_index = self._build_refmet_name_index(refmet_by_name)

        raw_metabolites = payload.get("metabolites", []) or []
        metabolite_names: list[str] = []
        metabolite_lookup_by_analysis: dict[str, dict[str, dict[str, Any]]] = {}
        metabolite_lookup_global: dict[str, dict[str, Any]] = {}
        if raw_metabolites:
            if isinstance(raw_metabolites[0], dict):
                metabolite_lookup_by_analysis, metabolite_lookup_global, metabolite_names = self._build_metabolite_lookup(
                    [item for item in raw_metabolites if isinstance(item, dict)]
                )
            else:
                metabolite_names = [str(item) for item in raw_metabolites if str(item).strip()]

        for index, datatable_path in enumerate(datatable_files, start=1):
            rows = self._read_table_rows(datatable_path)
            if not rows:
                continue
            columns = list(rows[0].keys())
            if len(columns) <= 2:
                continue
            # Detect transposed (features×samples) layout and pivot to samples×features.
            # For _Results.txt files, also apply a row-count heuristic: if the file
            # is not flagged as transposed by column name but has more rows than
            # columns (more features than samples), force-transpose it.
            is_results_file = self._table_kind(datatable_path) == "results"
            if self._is_transposed(columns) or (is_results_file and len(rows) > len(columns)):
                rows = self._pivot_matrix(rows, columns)
                if not rows:
                    continue
                columns = list(rows[0].keys())
            sample_column = columns[0]
            class_column = columns[1] if len(columns) > 1 else "Class"
            feature_columns = columns[2:]
            assay_key = str(index)
            resolution = resolution_by_path.get(str(datatable_path.resolve()), {})
            inferred_analysis_id = resolution.get("analysis_id") or self._infer_analysis_id(datatable_path, bundle["study_id"])
            source_name = resolution.get("selected_name") or datatable_path.name
            tabular_kind = resolution.get("selected_kind") or self._table_kind(datatable_path)
            assay_info = analysis_by_id.get(inferred_analysis_id, {})
            if not assay_info:
                assay_info = analyses.get(assay_key, {}) if isinstance(analyses.get(assay_key, {}), dict) else {}
            assay_id = slugify(inferred_analysis_id or datatable_path.stem)
            analysis_type_text = str(assay_info.get("analysis_type") or study.analysis_type or "").strip()
            analysis_type_upper = analysis_type_text.upper()
            is_nmr = "NMR" in analysis_type_upper and "MS" not in analysis_type_upper
            # Polarity: read ion_mode from analysis metadata; only write "mixed" if
            # both positive and negative are present across analyses.
            ion_mode_raw = (assay_info.get("ion_mode") or "").strip().upper()
            if ion_mode_raw in {"POSITIVE", "POS", "P"}:
                polarity = "positive"
            elif ion_mode_raw in {"NEGATIVE", "NEG", "N"}:
                polarity = "negative"
            elif ion_mode_raw:
                polarity = ion_mode_raw  # preserve raw string, e.g. "UNSPECIFIED"
            else:
                polarity = ""
            # Units from analysis metadata
            units = (assay_info.get("units") or "").strip()
            assay = AssayRecord(
                assay_id=assay_id,
                name=source_name,
                platform=analysis_type_text or study.analysis_type,
                polarity=polarity,
                technology="nuclear magnetic resonance" if is_nmr else "mass spectrometry",
                measurement_type="metabolite profiling",
                feature_matrix_id=assay_id,
                metadata={
                    "analysis": assay_info,
                    "reported_n_metabolites": str(len(feature_columns)),
                    "units": units,
                    "analysis_id": assay_info.get("analysis_id", inferred_analysis_id),
                    "analysis_type": analysis_type_text,
                    "chromatography_type": assay_info.get("chromatography_type", ""),
                    "chromatography_system": assay_info.get("chromatography_system", ""),
                    "chromatography_column": assay_info.get("chromatography_column", ""),
                    "ms_type": assay_info.get("ms_type", ""),
                    "ms_instrument_type": assay_info.get("ms_instrument_type", ""),
                    "ms_instrument_name": assay_info.get("ms_instrument_name", ""),
                    "nmr_experiment_type": assay_info.get("nmr_experiment_type", ""),
                    "nmr_instrument_type": assay_info.get("nmr_instrument_type", ""),
                    "nmr_spectrometer_frequency": assay_info.get("nmr_spectrometer_frequency", ""),
                    "nmr_solvent": assay_info.get("nmr_solvent", ""),
                    "nmr_pulse_sequence": assay_info.get("nmr_pulse_sequence", ""),
                    "nmr_water_suppression": assay_info.get("nmr_water_suppression", ""),
                    "nmr_reference_compound": assay_info.get("nmr_reference_compound", ""),
                    "nmr_temperature": assay_info.get("nmr_temperature", ""),
                    "nmr_data_block": assay_info.get("nmr_data_block", ""),
                    "tabular_kind": tabular_kind,
                    "data_format": assay_info.get("data_format", ""),
                },
            )
            assays.append(assay)

            sample_ids: list[str] = []
            values: list[list[float | None]] = []
            labels: dict[str, str] = {}
            for row in rows:
                sample_id = (row.get(sample_column) or "").strip()
                if not sample_id:
                    continue
                raw_label = row.get(class_column, "")
                raw_label_text = str(raw_label or "").strip()
                if self._is_artifact_sample_row(sample_id, raw_label):
                    continue
                # Skip header/label artifact rows: all non-empty feature values are strings
                non_empty = {f: row.get(f) for f in feature_columns if row.get(f) not in ("", None)}
                if non_empty and all(safe_float(v) is None for v in non_empty.values()):
                    continue
                # Keep exact source-native sample IDs from the active matrix.
                # Avoid cross-namespace alias collapsing (e.g., BCJ* -> KS*).
                canonical_id = sample_id
                # Tabular data is the authoritative label source; preserve the
                # full factor string as one class label.
                tabular_label = self._compose_class_label(raw_label_text) or (
                    (self._value_for_key(raw_label, label_key) if label_key else "") or self._extract_primary_label(raw_label)
                )
                # Derive fallback label from factors metadata only when this exact
                # sample ID exists there and tabular label is empty.
                endpoint_label = ""
                if not tabular_label and canonical_id in factor_lookup:
                    ftext = factor_lookup[canonical_id].get("factors", "")
                    endpoint_label = self._compose_class_label(ftext) or (
                        (self._value_for_key(ftext, label_key) if label_key else "") or self._extract_primary_label(ftext)
                    )
                label = tabular_label or endpoint_label
                labels[canonical_id] = label or "unknown"
                sample_ids.append(canonical_id)
                class_string = raw_label_text or endpoint_label or tabular_label or ""
                factor_string = factor_lookup.get(canonical_id, {}).get("factors", "")
                if canonical_id not in sample_lookup:
                    attrs: dict[str, str] = {"class_string": class_string}
                    if factor_string:
                        attrs["factor_string"] = factor_string
                    if endpoint_label and endpoint_label != tabular_label:
                        attrs["endpoint_label"] = endpoint_label
                        attrs["endpoint_label_key"] = label_key or ""
                    if tabular_label:
                        attrs["tabular_primary_label"] = tabular_label
                    _stype = factor_lookup.get(canonical_id, {}).get("sample_source", "")
                    sample_lookup[canonical_id] = SampleRecord(
                        sample_id=canonical_id,
                        label=labels[canonical_id],
                        disease="",
                        sample_type=_stype,
                        organism=study.organism,
                        organism_part=_stype,
                        attributes=attrs,
                        source_file=source_name,
                    )
                    samples.append(sample_lookup[canonical_id])
                else:
                    existing = sample_lookup[canonical_id]
                    attrs = existing.attributes if isinstance(existing.attributes, dict) else {}
                    if class_string and not str(attrs.get("class_string", "")).strip():
                        attrs["class_string"] = class_string
                    if tabular_label and not str(attrs.get("tabular_primary_label", "")).strip():
                        attrs["tabular_primary_label"] = tabular_label
                    if factor_string and not str(attrs.get("factor_string", "")).strip():
                        attrs["factor_string"] = factor_string
                    if endpoint_label and endpoint_label != tabular_label and not str(attrs.get("endpoint_label", "")).strip():
                        attrs["endpoint_label"] = endpoint_label
                        attrs["endpoint_label_key"] = label_key or ""
                    existing.attributes = attrs
                values.append([safe_float(row.get(feature)) for feature in feature_columns])

            feature_ids: list[str] = []
            for feature_index, feature in enumerate(feature_columns, start=1):
                feature_id = f"{assay_id}::f{feature_index}"
                feature_ids.append(feature_id)
                feature_text = str(feature or "").strip()
                feature_class = classify_feature_name(feature_text)
                lookup_keys: list[str] = []
                normalized_name = normalize_label(feature_text)
                feature_key = normalize_label(feature_text)
                if feature_key and feature_key != "unknown":
                    lookup_keys.append(feature_key)
                mzrt_key = mzrt_lookup_key(feature_text)
                if mzrt_key:
                    lookup_keys.append(mzrt_key)
                analysis_lookup = metabolite_lookup_by_analysis.get(str(inferred_analysis_id).upper(), {})
                row_match = None
                for key in lookup_keys:
                    row_match = analysis_lookup.get(key)
                    if isinstance(row_match, dict):
                        break
                    row_match = metabolite_lookup_global.get(key)
                    if isinstance(row_match, dict):
                        break
                refmet_name = ""
                refmet_id = ""
                database_identifier = ""
                chemical_formula = ""
                smiles = ""
                inchi = ""
                mapped_reference_id = normalized_name
                mapping_confidence = 0.9 if normalized_name != "unknown" else 0.2
                ambiguity_flags = []
                if feature_text.lower() in {"unknown", "na", "n/a"}:
                    ambiguity_flags.append("unknown_identification")
                if ";" in feature_text and not feature_class["is_mz_rt"]:
                    ambiguity_flags.append("multi_candidate_name")
                    ambiguity_flags.append("multi_candidate_name_semicolon")
                elif (
                    "/" in feature_text
                    and not feature_class["is_mz_rt"]
                    and not looks_like_lipid_structural_name(feature_text)
                ):
                    ambiguity_flags.append("multi_candidate_name")
                    ambiguity_flags.append("multi_candidate_name_slash")
                if feature_class["is_mz_rt"]:
                    ambiguity_flags.append("mz_rt_feature")
                    mapping_confidence = min(mapping_confidence, 0.25)
                    canonical = str(feature_class.get("canonical_mzrt") or "").strip()
                    if canonical:
                        mapped_reference_id = f"mzrt:{canonical}"
                elif feature_class["is_non_metabolite"]:
                    ambiguity_flags.append("non_metabolite_feature")
                    mapping_confidence = min(mapping_confidence, 0.2)
                if isinstance(row_match, dict):
                    refmet_name = str(row_match.get("refmet_name", "") or "").strip()
                    refmet_details = row_match.get("refmet_details")
                    if isinstance(refmet_details, list) and refmet_details and isinstance(refmet_details[0], dict):
                        detail = refmet_details[0]
                        refmet_id = str(detail.get("refmet_id", "") or "").strip()
                        if not chemical_formula:
                            chemical_formula = str(detail.get("formula", "") or "").strip()
                        if not inchi:
                            inchi = str(detail.get("inchi_key", "") or "").strip()
                        # Some dumps expose the ID keys in the RefMet detail block
                        hmdb_id = str(detail.get("hmdb_id", "") or "").strip()
                        pubchem_cid = str(detail.get("pubchem_cid", "") or "").strip()
                        chebi_id = str(detail.get("chebi_id", "") or "").strip()
                        kegg_id = str(detail.get("kegg_id", "") or "").strip()
                        if hmdb_id:
                            database_identifier = f"HMDB:{hmdb_id}"
                        elif chebi_id:
                            database_identifier = f"CHEBI:{chebi_id}"
                        elif kegg_id:
                            database_identifier = f"KEGG:{kegg_id}"
                        elif pubchem_cid:
                            database_identifier = f"PUBCHEM:{pubchem_cid}"
                        elif refmet_id:
                            database_identifier = f"REFMET:{refmet_id}"
                    compound_details = row_match.get("compound_details")
                    if isinstance(compound_details, list) and compound_details and isinstance(compound_details[0], dict):
                        compound = compound_details[0]
                        hmdb_id = str(compound.get("hmdb_id", "") or "").strip()
                        chebi_id = str(compound.get("chebi_id", "") or "").strip()
                        kegg_id = str(compound.get("kegg_id", "") or "").strip()
                        pubchem_cid = str(compound.get("pubchem_cid", "") or "").strip()
                        if not database_identifier:
                            if hmdb_id:
                                database_identifier = f"HMDB:{hmdb_id}"
                            elif chebi_id:
                                database_identifier = f"CHEBI:{chebi_id}"
                            elif kegg_id:
                                database_identifier = f"KEGG:{kegg_id}"
                            elif pubchem_cid:
                                database_identifier = f"PUBCHEM:{pubchem_cid}"
                        if not chemical_formula:
                            chemical_formula = str(compound.get("formula", "") or "").strip()
                        if not inchi:
                            inchi = str(compound.get("inchi_key", "") or "").strip()
                        if not smiles:
                            smiles = str(compound.get("smiles", "") or "").strip()
                    # Direct fields added by enrich_metabolites_refmet.py (last fallback)
                    if not refmet_id:
                        refmet_id = str(row_match.get("refmet_id", "") or "").strip()
                    if not chemical_formula:
                        chemical_formula = str(row_match.get("formula", "") or "").strip()
                    if not inchi:
                        inchi = str(row_match.get("inchi_key", "") or "").strip()
                    if not database_identifier:
                        for _id_key, _prefix in (
                            ("hmdb_id", "HMDB"), ("chebi_id", "CHEBI"),
                            ("kegg_id", "KEGG"), ("pubchem_cid", "PUBCHEM"),
                        ):
                            _val = str(row_match.get(_id_key, "") or "").strip()
                            if _val:
                                database_identifier = f"{_prefix}:{_val}"
                                break
                        if not database_identifier and refmet_id:
                            database_identifier = f"REFMET:{refmet_id}"
                    refmet_match_count = row_match.get("refmet_match_count")
                    try:
                        if int(refmet_match_count or 0) > 1:
                            if "multi_candidate_name" not in ambiguity_flags:
                                ambiguity_flags.append("multi_candidate_name")
                            ambiguity_flags.append("multi_candidate_name_refmet")
                    except (TypeError, ValueError):
                        pass
                    if refmet_name:
                        ambiguity_flags = [
                            flag for flag in ambiguity_flags
                            if flag not in {"mz_rt_feature", "non_metabolite_feature"}
                        ]
                        normalized_name = normalize_label(refmet_name)
                        mapped_reference_id = normalize_label(refmet_name)
                        mapping_confidence = max(mapping_confidence, 0.98)
                    elif refmet_id:
                        ambiguity_flags = [
                            flag for flag in ambiguity_flags
                            if flag not in {"mz_rt_feature", "non_metabolite_feature"}
                        ]
                        mapped_reference_id = refmet_id
                        mapping_confidence = max(mapping_confidence, 0.95)
                    elif database_identifier:
                        ambiguity_flags = [
                            flag for flag in ambiguity_flags
                            if flag not in {"mz_rt_feature", "non_metabolite_feature"}
                        ]
                        mapped_reference_id = database_identifier
                        mapping_confidence = max(mapping_confidence, 0.92)
                annotation = MetaboliteAnnotationRecord(
                    feature_id=feature_id,
                    raw_name=feature_text,
                    normalized_name=normalized_name,
                    database_identifier=database_identifier,
                    mapped_reference_id=mapped_reference_id,
                    mapping_confidence=mapping_confidence,
                    chemical_formula=chemical_formula,
                    smiles=smiles,
                    inchi=inchi,
                    ambiguity_flags=ambiguity_flags,
                )
                annotations.append(annotation)
                mapping_namespace = "lexical"
                if refmet_name or refmet_id:
                    mapping_namespace = "refmet"
                elif "mz_rt_feature" in ambiguity_flags:
                    mapping_namespace = "mzrt_feature"
                elif "non_metabolite_feature" in ambiguity_flags:
                    mapping_namespace = "non_metabolite_feature"
                mappings.append(
                    MappingRecord(
                        raw_identifier=feature_text,
                        normalized_name=normalized_name,
                        mapped_reference_id=mapped_reference_id,
                        mapping_confidence=mapping_confidence,
                        namespace=mapping_namespace,
                    )
                )

            # Compute per-analysis RefMet chemical class distribution
            class_dist: dict[str, int] = {}
            for feature in feature_columns:
                feature_text = str(feature or "").strip()
                feature_key = normalize_label(feature_text)
                lookup_keys: list[str] = []
                if feature_key and feature_key != "unknown":
                    lookup_keys.append(feature_key)
                mzrt_key = mzrt_lookup_key(feature_text)
                if mzrt_key:
                    lookup_keys.append(mzrt_key)
                analysis_lookup = metabolite_lookup_by_analysis.get(str(inferred_analysis_id).upper(), {})
                row_match = None
                for key in lookup_keys:
                    row_match = analysis_lookup.get(key)
                    if isinstance(row_match, dict):
                        break
                    row_match = metabolite_lookup_global.get(key)
                    if isinstance(row_match, dict):
                        break
                sc = self._metabolite_row_super_class(
                    row_match,
                    refmet_by_name,
                    refmet_name_index,
                ) if isinstance(row_match, dict) else ""
                if not sc:
                    sc = "Unclassified"
                class_dist[sc] = class_dist.get(sc, 0) + 1
            assay.metadata["class_distribution"] = class_dist

            feature_matrices.append(
                FeatureMatrix(
                    matrix_id=assay.feature_matrix_id,
                    assay_id=assay.assay_id,
                    sample_ids=sample_ids,
                    feature_ids=feature_ids,
                    values=values,
                    labels=labels,
                    source_file=source_name,
                    source_kind=tabular_kind,
                )
            )

        # Register any analyses from the JSON metadata that were not covered by a
        # datatable file (e.g. stub datatables with ≤2 columns that couldn't be
        # supplemented). These appear in the per-analysis table with no feature data.
        created_assay_ids = {a.assay_id for a in assays}
        for analysis_key, analysis_data in analyses.items():
            if not isinstance(analysis_data, dict):
                continue
            analysis_id = str(analysis_data.get("analysis_id") or "").strip().upper()
            if not analysis_id:
                continue
            assay_id = slugify(analysis_id)
            if assay_id in created_assay_ids:
                continue
            nm_raw = n_metabolites.get(analysis_id, n_metabolites.get(analysis_key, ""))
            if isinstance(nm_raw, dict):
                nm_raw = nm_raw.get("num_metabolites", "")
            analysis_type_text = str(analysis_data.get("analysis_type") or study.analysis_type or "").strip()
            analysis_type_upper = analysis_type_text.upper()
            is_nmr = "NMR" in analysis_type_upper and "MS" not in analysis_type_upper
            ion_mode_raw = (analysis_data.get("ion_mode") or "").strip().upper()
            if ion_mode_raw in {"POSITIVE", "POS", "P"}:
                stub_polarity = "positive"
            elif ion_mode_raw in {"NEGATIVE", "NEG", "N"}:
                stub_polarity = "negative"
            elif ion_mode_raw:
                stub_polarity = ion_mode_raw
            else:
                stub_polarity = ""
            stub_assay = AssayRecord(
                assay_id=assay_id,
                name=analysis_id,
                platform=analysis_type_text or study.analysis_type,
                polarity=stub_polarity,
                technology="nuclear magnetic resonance" if is_nmr else "mass spectrometry",
                measurement_type="metabolite profiling",
                feature_matrix_id=assay_id,
                metadata={
                    "analysis": analysis_data,
                    "reported_n_metabolites": nm_raw,
                    "units": (analysis_data.get("units") or "").strip(),
                    "analysis_id": analysis_id,
                    "analysis_type": analysis_type_text,
                    "chromatography_type": analysis_data.get("chromatography_type", ""),
                    "chromatography_system": analysis_data.get("chromatography_system", ""),
                    "chromatography_column": analysis_data.get("chromatography_column", ""),
                    "ms_type": analysis_data.get("ms_type", ""),
                    "ms_instrument_type": analysis_data.get("ms_instrument_type", ""),
                    "ms_instrument_name": analysis_data.get("ms_instrument_name", ""),
                    "nmr_experiment_type": analysis_data.get("nmr_experiment_type", ""),
                    "nmr_instrument_type": analysis_data.get("nmr_instrument_type", ""),
                    "nmr_spectrometer_frequency": analysis_data.get("nmr_spectrometer_frequency", ""),
                    "nmr_solvent": analysis_data.get("nmr_solvent", ""),
                    "nmr_pulse_sequence": analysis_data.get("nmr_pulse_sequence", ""),
                    "nmr_water_suppression": analysis_data.get("nmr_water_suppression", ""),
                    "nmr_reference_compound": analysis_data.get("nmr_reference_compound", ""),
                    "nmr_temperature": analysis_data.get("nmr_temperature", ""),
                    "nmr_data_block": analysis_data.get("nmr_data_block", ""),
                    "tabular_kind": "json_only",
                    "class_distribution": {},
                },
            )
            assays.append(stub_assay)
            created_assay_ids.add(assay_id)

        provenance = ProvenanceRecord(
            source=self.source_name,
            study_id=bundle["study_id"],
            source_root=bundle["source_root"],
            file_manifest=bundle["file_manifest"],
            parser_version=bundle.get("parser_version", __version__),
            connector_name=self.connector_name,
            file_hashes={path: sha256_file(path) for path in bundle["file_manifest"]},
            notes=[
                note
                for note in [
                    f"Metabolite catalog size: {len(metabolite_names)}",
                    bundle.get("tabular_message", ""),
                ]
                if note
            ],
        )

        canonical = CanonicalStudy(
            schema_version="1.0",
            study=study,
            samples=samples,
            assays=assays,
            feature_matrices=feature_matrices,
            annotations=annotations,
            mappings=mappings,
            provenance=provenance,
            score_defaults={
                "minimum_class_count": 5,
                "missingness_threshold": 0.25,
                "high_confidence_mapping": 0.8,
            },
        )
        provenance.content_hash = compute_content_hash(canonical)
        return canonical


def backfill_latest_dump_metabolites(
    *,
    root: str | Path | None = None,
    workspace: Path | None = None,
    study_ids: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    allow_remote: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    connector = MetabolomicsWorkbenchConnector()
    workspace = workspace or Path.cwd()
    source_root = connector._resolve_source_root(workspace, str(root) if root else None)
    if study_ids:
        targets = sorted({str(item).strip().upper() for item in study_ids if str(item).strip()})
    else:
        targets = sorted(
            study_path.name
            for study_path in source_root.glob("ST*")
            if study_path.is_dir() and connector._latest_manifest_file(source_root, study_path.name).exists()
        )
    if limit is not None:
        targets = targets[: max(0, limit)]

    results: list[dict[str, Any]] = []
    updated = skipped = empty = failed = 0
    for index, study_id in enumerate(targets, start=1):
        cache_path = connector._metabolites_cache_file(source_root, study_id)
        if cache_path.exists() and not force:
            skipped += 1
            result = {"study_id": study_id, "status": "skipped", "path": str(cache_path), "row_count": None}
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: skipped (cache exists)")
            continue
        try:
            rows, resolved_path = connector._resolve_latest_dump_metabolites_payload(
                source_root,
                study_id,
                allow_remote_metabolites_fetch=allow_remote,
            )
            row_count = len(rows)
            if row_count > 0:
                updated += 1
                status = "updated"
            else:
                empty += 1
                status = "empty"
            result = {
                "study_id": study_id,
                "status": status,
                "path": str(resolved_path),
                "row_count": row_count,
            }
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: {status} ({row_count} rows)")
        except Exception as exc:
            failed += 1
            result = {
                "study_id": study_id,
                "status": "failed",
                "path": str(cache_path),
                "error": str(exc),
            }
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: failed ({exc})")

    return {
        "root": str(source_root),
        "requested_study_count": len(targets),
        "updated_study_count": updated,
        "skipped_study_count": skipped,
        "empty_study_count": empty,
        "failed_study_count": failed,
        "allow_remote": allow_remote,
        "force": force,
        "results": results,
    }


def backfill_latest_dump_disease(
    *,
    root: str | Path | None = None,
    workspace: Path | None = None,
    study_ids: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    allow_remote: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    connector = MetabolomicsWorkbenchConnector()
    workspace = workspace or Path.cwd()
    source_root = connector._resolve_source_root(workspace, str(root) if root else None)
    if study_ids:
        targets = sorted({str(item).strip().upper() for item in study_ids if str(item).strip()})
    else:
        targets = sorted(
            study_path.name
            for study_path in source_root.glob("ST*")
            if study_path.is_dir() and connector._latest_manifest_file(source_root, study_path.name).exists()
        )
    if limit is not None:
        targets = targets[: max(0, limit)]

    results: list[dict[str, Any]] = []
    updated = skipped = empty = failed = 0
    for index, study_id in enumerate(targets, start=1):
        cache_path = connector._disease_cache_file(source_root, study_id)
        if cache_path.exists() and not force:
            skipped += 1
            result = {"study_id": study_id, "status": "skipped", "path": str(cache_path), "row_count": None}
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: skipped (cache exists)")
            continue
        try:
            payload, resolved_path = connector._resolve_latest_dump_disease_payload(
                source_root,
                study_id,
                allow_remote_disease_fetch=allow_remote,
            )
            row_count = 1 if isinstance(payload, dict) and payload else 0
            if row_count > 0:
                updated += 1
                status = "updated"
            else:
                empty += 1
                status = "empty"
            result = {
                "study_id": study_id,
                "status": status,
                "path": str(resolved_path),
                "row_count": row_count,
            }
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: {status} ({row_count} row)")
        except Exception as exc:
            failed += 1
            result = {
                "study_id": study_id,
                "status": "failed",
                "path": str(cache_path),
                "error": str(exc),
            }
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: failed ({exc})")

    return {
        "root": str(source_root),
        "requested_study_count": len(targets),
        "updated_study_count": updated,
        "skipped_study_count": skipped,
        "empty_study_count": empty,
        "failed_study_count": failed,
        "allow_remote": allow_remote,
        "force": force,
        "results": results,
    }


def backfill_latest_dump_factors(
    *,
    root: str | Path | None = None,
    workspace: Path | None = None,
    study_ids: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    allow_remote: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    connector = MetabolomicsWorkbenchConnector()
    workspace = workspace or Path.cwd()
    source_root = connector._resolve_source_root(workspace, str(root) if root else None)
    if study_ids:
        targets = sorted({str(item).strip().upper() for item in study_ids if str(item).strip()})
    else:
        targets = sorted(
            study_path.name
            for study_path in source_root.glob("ST*")
            if study_path.is_dir() and connector._latest_manifest_file(source_root, study_path.name).exists()
        )
    if limit is not None:
        targets = targets[: max(0, limit)]

    results: list[dict[str, Any]] = []
    updated = skipped = empty = failed = 0
    for index, study_id in enumerate(targets, start=1):
        cache_path = connector._factors_cache_file(source_root, study_id)
        if cache_path.exists() and not force:
            skipped += 1
            result = {"study_id": study_id, "status": "skipped", "path": str(cache_path), "row_count": None}
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: skipped (cache exists)")
            continue
        try:
            manifest_path = connector._latest_manifest_file(source_root, study_id)
            analysis_json_paths: dict[str, Path] = {}
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                analyses = manifest.get("analyses", {}) if isinstance(manifest, dict) else {}
                if isinstance(analyses, dict):
                    for analysis_id, item in analyses.items():
                        if not isinstance(item, dict):
                            continue
                        rel_json = item.get("mwtab_json")
                        if not rel_json:
                            continue
                        json_path = source_root / rel_json
                        if json_path.exists() and json_path.is_file():
                            analysis_json_paths[str(analysis_id)] = json_path
            payload, resolved_path = connector._resolve_latest_dump_factors_payload(
                source_root,
                study_id,
                analysis_json_paths,
                allow_remote_factors_fetch=allow_remote,
            )
            row_count = len(payload)
            if row_count > 0:
                updated += 1
                status = "updated"
            else:
                empty += 1
                status = "empty"
            result = {
                "study_id": study_id,
                "status": status,
                "path": str(resolved_path),
                "row_count": row_count,
            }
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: {status} ({row_count} rows)")
        except Exception as exc:
            failed += 1
            result = {
                "study_id": study_id,
                "status": "failed",
                "path": str(cache_path),
                "error": str(exc),
            }
            results.append(result)
            if verbose:
                print(f"[{index}/{len(targets)}] {study_id}: failed ({exc})")

    return {
        "root": str(source_root),
        "requested_study_count": len(targets),
        "updated_study_count": updated,
        "skipped_study_count": skipped,
        "empty_study_count": empty,
        "failed_study_count": failed,
        "allow_remote": allow_remote,
        "force": force,
        "results": results,
    }
