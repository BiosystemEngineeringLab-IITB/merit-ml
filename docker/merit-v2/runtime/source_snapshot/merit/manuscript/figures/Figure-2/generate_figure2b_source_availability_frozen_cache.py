#!/usr/bin/python3
"""Figure 2B (revision): per-source tabular availability across the 6,696 MW analyses.

Every count is computed from the frozen MERIT cache; nothing is hard-coded.
  cache : ML-ready/mw-dump-latest-18thSep-2026-version  (read-only; must hold 42,559 files)
  rule  : SI Text S1 (revised wording)
    - at least one value column beyond the identifier/label columns
        datatable, untarg_data (samples x features): beyond Samples + Class/group
        mwTab text (features x samples): at least one sample column beyond the feature column
    - at least one data row with at least one numerically parseable value in a value column;
      missing tokens are not numeric; malformed exponents (1.80.E-04) normalized to 1.80E-04
    - mwTab rows must lie in a recognized *_DATA_START/_END block (Table S2); "Factors" row excluded
  classes: valid / file present but invalid or empty / no file present

Changes from the submitted panel:
  - datatable: the 1,824 non-valid analyses have a /datatable/ file with only Samples + Class
    columns, so they are "file present but invalid/empty" (orange), not "no file present".
  - untarg_data: "no file present" is 4,804 (the submitted label said 4,806, so the bar summed to 6,698).
    untarg_data is an analysis-level endpoint (/analysis_id/<AN>/untarg_data): an AN filed under two
    studies (AN004586, AN007493, AN007494) has one table, stored once in the cache, and it is counted for
    both study-analysis pairs, exactly as mwTab and datatable are. This keeps 1,887 valid.
  - one stacking order for all bars: valid, invalid/empty, no file.

Outputs (Used-in-manuscript/): figure2b_source_availability_frozen_cache.{pdf,png,svg}
                               figure2b_source_availability_counts.tsv
                               figure2b_source_availability_per_analysis.tsv  (basis for Tables S5-S7)
"""
from __future__ import annotations

import csv
import math
import os
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt

CACHE = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/mw-dump-latest-18thSep-2026-version")
OUT = Path(__file__).resolve().parent / "Used-in-manuscript"
EXPECTED_FILES = 42559

BLOCKS = ("MS_METABOLITE_DATA", "NMR_METABOLITE_DATA", "NMR_BINNED_DATA",
          "EXTENDED_NMR_METABOLITE_DATA", "EXTENDED_MS_METABOLITE_DATA",
          "DIRECT_INFUSION_METABOLITE_DATA", "METABOLITE_DATA")
STARTS = {f"{b}_START" for b in BLOCKS}
ENDS = {f"{b}_END" for b in BLOCKS}
MALFORMED_EXP = re.compile(r"^([+-]?\d+(?:\.\d+)?)\.([eE][+-]?\d+)$")
VALID, INVALID, ABSENT = "valid", "invalid_or_empty", "no_file"


# ------------------------------------------------------------------ validity rule
def is_numeric(token: str) -> bool:
    t = MALFORMED_EXP.sub(r"\1\2", token.strip())
    try:
        return math.isfinite(float(t))
    except ValueError:
        return False


def samples_by_features(path: Path, id_cols: int = 2) -> str:
    """datatable / untarg_data: Samples, Class|group, then feature columns."""
    if not path.is_file():
        return ABSENT
    with path.open(encoding="utf-8", errors="replace") as fh:
        header = fh.readline().rstrip("\r\n").split("\t")
        if len(header) <= id_cols:
            return INVALID
        for line in fh:
            if any(is_numeric(c) for c in line.rstrip("\r\n").split("\t")[id_cols:]):
                return VALID
    return INVALID


def mwtab_text(path: Path) -> str:
    """mwTab text: feature rows x sample columns inside recognized data blocks."""
    if not path.is_file():
        return ABSENT
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() in STARTS:
            header = lines[i + 1].split("\t") if i + 1 < len(lines) else []
            i += 2
            while i < len(lines) and lines[i].strip() not in ENDS:
                row = lines[i].split("\t")
                name = row[0].strip()
                if len(header) >= 2 and name and name.lower() != "factors" and any(is_numeric(c) for c in row[1:]):
                    return VALID
                i += 1
        i += 1
    return INVALID


# ------------------------------------------------------------------ counting
def untarg_path(an_dir: Path) -> Path:
    """untarg_data is keyed by analysis ID alone, so a cross-listed AN shares one table.

    Use the pair's own copy if present, otherwise the same AN's copy under another study in the
    frozen cache (the original pipeline stored it once and linked it from both studies).
    """
    an = an_dir.name
    own = an_dir / "tabular" / f"{an}_untarg_data.tsv"
    if own.is_file():
        return own
    shared = sorted(CACHE.glob(f"ST*/{an}/tabular/{an}_untarg_data.tsv"))
    return shared[0] if shared else own


def classify_corpus() -> list[dict]:
    n_files = sum(len(files) for _, _, files in os.walk(CACHE))
    if n_files != EXPECTED_FILES:
        raise SystemExit(f"frozen cache changed: {n_files} files, expected {EXPECTED_FILES}")
    rows = []
    for st in sorted(p for p in CACHE.glob("ST*") if p.is_dir()):
        for an_dir in sorted(p for p in st.glob("AN*") if p.is_dir()):
            an = an_dir.name
            rows.append({
                "study_id": st.name, "analysis_id": an,
                "mwtab": mwtab_text(an_dir / "mwtab" / f"{an}_mwtab.txt"),
                "datatable": samples_by_features(an_dir / "tabular" / f"{an}_datatable_slash.txt"),
                "untarg_data": samples_by_features(untarg_path(an_dir)),
            })
    return rows


# ------------------------------------------------------------------ plotting
C = {VALID: "#2A9D6A", INVALID: "#E09C3A", ABSENT: "#C0392B"}
LEGEND = {VALID: "Valid tabular matrix", INVALID: "File present but invalid/empty", ABSENT: "No file present"}
SMALL = 250  # segments shorter than this are labelled outside the bar


def pct_label(n: int, total: int, sep: str) -> str:
    return f"{100 * n / total:.1f}%{sep}(N={n:,})"


def plot(counts: dict[str, dict[str, int]], total: int) -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.weight": "bold",
                         "axes.labelweight": "bold", "figure.dpi": 300,
                         "pdf.fonttype": 42, "svg.fonttype": "none"})
    fig, ax = plt.subplots(figsize=(6.5, 6.0), facecolor="white")
    labels = ["mwtab.txt", "datatable", "untarg_data"]
    keys = ["mwtab", "datatable", "untarg_data"]
    width = 0.52

    for x, key in enumerate(keys):
        bottom = 0
        for status in (VALID, INVALID, ABSENT):
            n = counts[key][status]
            if n == 0:
                continue
            ax.bar(x, n, width, bottom=bottom, color=C[status], zorder=3)
            mid = bottom + n / 2
            if n >= SMALL:
                ax.text(x, mid, pct_label(n, total, "\n"), ha="center", va="center",
                        color="white", fontsize=11, fontweight="bold", zorder=4)
            elif bottom + n >= total - 1:  # sliver on top of the bar: callout above
                ax.plot([x, x], [total, total + 250], color=C[status], lw=1.0, zorder=4)
                ax.text(x, total + 300, pct_label(n, total, " "), ha="center", va="bottom",
                        color=C[status], fontsize=10.5, fontweight="bold")
            else:  # sliver inside the bar: callout to the right
                x0 = x + width / 2
                ax.plot([x0, x0 + 0.1], [mid, mid], color=C[status], lw=1.0, zorder=4, clip_on=False)
                ax.text(x0 + 0.12, mid, pct_label(n, total, "\n"), ha="left", va="center",
                        color=C[status], fontsize=10.5, fontweight="bold", clip_on=False)
            bottom += n

    ax.set_xlim(-0.5, 2.5)
    ax.set_ylim(0, 8000)
    ax.yaxis.grid(True, linestyle="--", alpha=0.5, color="#D1D5DB", zorder=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#1F2937")
        ax.spines[side].set_linewidth(1.2)
    ax.set_ylabel("Analyses count", fontsize=13, fontweight="bold", labelpad=8)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=12, fontweight="bold")
    ax.tick_params(axis="both", which="major", labelsize=11, width=1.2)
    ax.text(-0.16, 1.05, "B", transform=ax.transAxes, fontsize=24, fontweight="bold", va="top", ha="left",
            family="serif")  # composite panel letters are serif
    handles = [mpatches.Patch(facecolor=C[s], label=LEGEND[s]) for s in (VALID, INVALID, ABSENT)]
    ax.legend(handles=handles, loc="upper center", ncol=3, fontsize=8.5, frameon=True,
              framealpha=0.95, edgecolor="#D1D5DB", bbox_to_anchor=(0.5, 1.0))

    fig.tight_layout()
    for ext in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"figure2b_source_availability_frozen_cache.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    rows = classify_corpus()
    total = len(rows)
    assert total == 6696, total
    keys = ("mwtab", "datatable", "untarg_data")
    counts = {k: {s: sum(r[k] == s for r in rows) for s in (VALID, INVALID, ABSENT)} for k in keys}
    any_valid = sum(any(r[k] == VALID for k in keys) for r in rows)

    with (OUT / "figure2b_source_availability_per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["study_id", "analysis_id", *keys], delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with (OUT / "figure2b_source_availability_counts.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["source", VALID, INVALID, ABSENT, "total"])
        for k in keys:
            w.writerow([k, *(counts[k][s] for s in (VALID, INVALID, ABSENT)), total])
        w.writerow(["any_source_valid", any_valid, "", total - any_valid, total])

    for k in keys:
        print(f"{k:12s}", counts[k])
    print(f"any source valid: {any_valid} ({100 * any_valid / total:.1f}%) | none: {total - any_valid}")
    plot(counts, total)


if __name__ == "__main__":
    main()
