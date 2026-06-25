#!/usr/bin/env python3
"""
Standalone MW source verifier for analysis-level tabular availability.

Checks, per analysis ID:
  1) mwtab txt endpoint
  2) datatable endpoint
  3) untarg_data endpoint

Reports both:
  - source exists (HTTP 200 + non-empty body)
  - source valid (tabular validity rules)

This script is intentionally self-contained and does not import project code.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MWTAB_URL = "https://www.metabolomicsworkbench.org/rest/study/analysis_id/{analysis_id}/mwtab/txt"
DATATABLE_URL = "https://www.metabolomicsworkbench.org/rest/study/analysis_id/{analysis_id}/datatable/"
UNTARG_URL = "https://www.metabolomicsworkbench.org/rest/study/analysis_id/{analysis_id}/untarg_data/"


MWTAB_BLOCK_TAGS: Tuple[Tuple[str, str], ...] = (
    ("MS_METABOLITE_DATA_START", "MS_METABOLITE_DATA_END"),
    ("NMR_METABOLITE_DATA_START", "NMR_METABOLITE_DATA_END"),
    ("NMR_BINNED_DATA_START", "NMR_BINNED_DATA_END"),
    ("EXTENDED_MS_METABOLITE_DATA_START", "EXTENDED_MS_METABOLITE_DATA_END"),
    ("EXTENDED_NMR_METABOLITE_DATA_START", "EXTENDED_NMR_METABOLITE_DATA_END"),
)
MWTAB_START_TO_END = {s: e for s, e in MWTAB_BLOCK_TAGS}
MWTAB_END_TAGS = {e for _, e in MWTAB_BLOCK_TAGS}


@dataclass
class FetchResult:
    exists: int
    valid: int
    status_code: int
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify MW mwtab/datatable/untarg source existence and validity for analyses from TSV."
    )
    parser.add_argument(
        "--input-tsv",
        type=Path,
        default=Path("/home/shayantan/metabolomics/ML-ready/merit/manuscript/mw_6696_source_presence.tsv"),
        help="Input TSV with study_id and analysis_id columns.",
    )
    parser.add_argument(
        "--output-tsv",
        type=Path,
        default=Path("/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence_api_verified.tsv"),
        help="Where to write verification results TSV.",
    )
    parser.add_argument("--workers", type=int, default=24, help="Number of concurrent worker threads.")
    parser.add_argument("--timeout-seconds", type=float, default=20.0, help="Per-request timeout.")
    parser.add_argument("--retries", type=int, default=2, help="Number of retries after first attempt.")
    parser.add_argument(
        "--progress-every",
        type=int,
        default=200,
        help="Print progress after every N completed analyses.",
    )
    return parser.parse_args()


def read_analyses(input_tsv: Path) -> List[Tuple[int, str, str]]:
    rows: List[Tuple[int, str, str]] = []
    with input_tsv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"study_id", "analysis_id"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                f"Input TSV must include columns {sorted(required)}. Found: {reader.fieldnames}"
            )
        for idx, row in enumerate(reader):
            study_id = (row.get("study_id") or "").strip()
            analysis_id = (row.get("analysis_id") or "").strip()
            if not study_id or not analysis_id:
                continue
            rows.append((idx, study_id, analysis_id))
    if not rows:
        raise ValueError("No analyses found in input TSV.")
    return rows


def decode_body(raw: bytes) -> str:
    if not raw:
        return ""
    if len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B:
        try:
            return gzip.decompress(raw).decode("utf-8", errors="replace")
        except Exception:
            pass
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _has_float_cell(cells: List[str]) -> bool:
    for c in cells:
        v = c.strip()
        if not v:
            continue
        try:
            float(v)
            return True
        except Exception:
            pass
        # Handle European MW scientific notation: X.YY.E±ZZ → X.YYE±ZZ
        try:
            import re as _re
            fixed = _re.sub(r"\.E([+-])", r"E\1", v)
            if fixed != v:
                float(fixed)
                return True
        except Exception:
            pass
    return False


def valid_tabular_text(text: str) -> int:
    # Rule from prior discussion:
    #   - header has >2 tab-separated columns
    #   - at least one non-header row contains a float-parseable value
    lines = [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0
    header_cols = lines[0].split("\t")
    if len(header_cols) <= 2:
        return 0
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split("\t")]
        if _has_float_cell(cells):
            return 1
    return 0


def valid_mwtab_text(text: str) -> int:
    # Rule from prior discussion:
    #   - at least one non-header line inside recognized metabolite block
    #   - that line must contain a float-parseable value
    current_end_tag: Optional[str] = None
    block_lines: List[str] = []

    def block_valid(lines: List[str]) -> bool:
        nonempty = [ln for ln in lines if ln.strip()]
        if len(nonempty) < 2:
            return False
        for ln in nonempty[1:]:
            cells = [c.strip() for c in ln.split("\t")]
            if _has_float_cell(cells):
                return True
        return False

    for raw_ln in text.splitlines():
        ln = raw_ln.strip()

        if current_end_tag is None and ln in MWTAB_START_TO_END:
            current_end_tag = MWTAB_START_TO_END[ln]
            block_lines = []
            continue

        if current_end_tag is not None:
            if ln == current_end_tag or ln in MWTAB_END_TAGS:
                if block_valid(block_lines):
                    return 1
                current_end_tag = None
                block_lines = []
                continue
            block_lines.append(raw_ln.rstrip("\r\n"))

    # Handle malformed files with a start tag but missing explicit end tag.
    if current_end_tag is not None and block_lines:
        if block_valid(block_lines):
            return 1
    return 0


def fetch_text(
    url: str,
    timeout_seconds: float,
    retries: int,
) -> Tuple[int, str, int, str]:
    """
    Returns: (http_status, decoded_text, exists, error)
      exists = 1 only for 200 + non-empty body
    """
    headers = {
        "User-Agent": "MERIT-API-Verifier/1.0",
        "Accept-Encoding": "gzip",
    }

    last_error = ""
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout_seconds) as resp:
                status = int(getattr(resp, "status", 200))
                raw = resp.read()
            text = decode_body(raw)
            exists = 1 if status == 200 and bool(text.strip()) else 0
            return status, text, exists, ""
        except HTTPError as e:
            last_error = f"HTTP {e.code}"
            status = int(getattr(e, "code", 0) or 0)
            if 500 <= status < 600 and attempt < retries:
                time.sleep(0.5 * (2**attempt))
                continue
            return status, "", 0, last_error
        except URLError as e:
            last_error = str(e.reason)
            if attempt < retries:
                time.sleep(0.5 * (2**attempt))
                continue
            return 0, "", 0, last_error
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            if attempt < retries:
                time.sleep(0.5 * (2**attempt))
                continue
            return 0, "", 0, last_error

    return 0, "", 0, last_error or "unknown error"


def verify_analysis(
    idx: int,
    study_id: str,
    analysis_id: str,
    timeout_seconds: float,
    retries: int,
) -> Dict[str, str]:
    mwtab_status, mwtab_text, mwtab_exists, mwtab_err = fetch_text(
        MWTAB_URL.format(analysis_id=analysis_id),
        timeout_seconds=timeout_seconds,
        retries=retries,
    )
    dat_status, dat_text, dat_exists, dat_err = fetch_text(
        DATATABLE_URL.format(analysis_id=analysis_id),
        timeout_seconds=timeout_seconds,
        retries=retries,
    )
    unt_status, unt_text, unt_exists, unt_err = fetch_text(
        UNTARG_URL.format(analysis_id=analysis_id),
        timeout_seconds=timeout_seconds,
        retries=retries,
    )

    mwtab_valid = valid_mwtab_text(mwtab_text) if mwtab_exists else 0
    dat_valid = valid_tabular_text(dat_text) if dat_exists else 0
    unt_valid = valid_tabular_text(unt_text) if unt_exists else 0

    return {
        "_idx": str(idx),
        "study_id": study_id,
        "analysis_id": analysis_id,
        "mwtab_api_exists": str(mwtab_exists),
        "mwtab_api_valid": str(mwtab_valid),
        "datatable_api_exists": str(dat_exists),
        "datatable_api_valid": str(dat_valid),
        "untarg_api_exists": str(unt_exists),
        "untarg_api_valid": str(unt_valid),
        "combo_valid_mw_dt_ut": f"{mwtab_valid}{dat_valid}{unt_valid}",
        "mwtab_status_code": str(mwtab_status),
        "datatable_status_code": str(dat_status),
        "untarg_status_code": str(unt_status),
        "mwtab_error": mwtab_err,
        "datatable_error": dat_err,
        "untarg_error": unt_err,
    }


def write_output(output_tsv: Path, rows: Iterable[Dict[str, str]]) -> None:
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "study_id",
        "analysis_id",
        "mwtab_api_exists",
        "mwtab_api_valid",
        "datatable_api_exists",
        "datatable_api_valid",
        "untarg_api_exists",
        "untarg_api_valid",
        "combo_valid_mw_dt_ut",
        "mwtab_status_code",
        "datatable_status_code",
        "untarg_status_code",
        "mwtab_error",
        "datatable_error",
        "untarg_error",
    ]
    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def print_summary(rows: List[Dict[str, str]], elapsed_s: float) -> None:
    n = len(rows)

    def c(key: str, value: str = "1") -> int:
        return sum(1 for r in rows if r.get(key) == value)

    print("\n=== Verification Summary ===")
    print(f"Analyses checked: {n}")
    if elapsed_s > 0:
        print(f"Elapsed: {elapsed_s:.1f}s ({(n/elapsed_s):.2f} analyses/s)")
    else:
        print(f"Elapsed: {elapsed_s:.1f}s")
    print(f"mwtab exists/valid: {c('mwtab_api_exists')}/{c('mwtab_api_valid')}")
    print(f"datatable exists/valid: {c('datatable_api_exists')}/{c('datatable_api_valid')}")
    print(f"untarg exists/valid: {c('untarg_api_exists')}/{c('untarg_api_valid')}")


def main() -> int:
    args = parse_args()
    analyses = read_analyses(args.input_tsv)
    total = len(analyses)
    print(f"Loaded {total} analyses from: {args.input_tsv}")
    print(
        f"Running API verification with workers={args.workers}, "
        f"timeout={args.timeout_seconds}s, retries={args.retries}"
    )

    start = time.time()
    out_rows: List[Optional[Dict[str, str]]] = [None] * total
    completed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                verify_analysis,
                idx,
                study_id,
                analysis_id,
                args.timeout_seconds,
                args.retries,
            )
            for idx, study_id, analysis_id in analyses
        ]

        for fut in as_completed(futures):
            row = fut.result()
            idx = int(row["_idx"])
            out_rows[idx] = row
            completed += 1

            if completed % args.progress_every == 0 or completed == total:
                elapsed = time.time() - start
                rate = completed / elapsed if elapsed > 0 else 0.0
                eta = (total - completed) / rate if rate > 0 else float("inf")
                print(
                    f"[{completed:>4}/{total}] "
                    f"{rate:.2f} analyses/s | ETA {eta/60:.1f} min"
                )

    final_rows = [r for r in out_rows if r is not None]
    elapsed = time.time() - start
    write_output(args.output_tsv, final_rows)
    print(f"\nWrote: {args.output_tsv}")
    print_summary(final_rows, elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
