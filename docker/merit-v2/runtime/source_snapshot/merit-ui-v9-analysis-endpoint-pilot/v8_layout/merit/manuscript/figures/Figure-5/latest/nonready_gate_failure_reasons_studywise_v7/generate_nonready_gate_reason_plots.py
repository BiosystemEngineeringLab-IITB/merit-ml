#!/usr/bin/env python3
"""Study-level gate-failure summaries for non-Ready MERIT v7 studies."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

GATE_ORDER = ["G1", "G2", "G3", "G4", "G5"]
GATE_LABELS = {
    "G1": "G1 no usable\nmatrix",
    "G2": "G2 biological\nsample count",
    "G3": "G3 deposited\ngroups",
    "G4": "G4 minimum\nclass support",
    "G5": "G5 median\nmissingness",
}
GATE_SHORT = {
    "G1": "no usable matrix",
    "G2": "biological sample count",
    "G3": "deposited groups",
    "G4": "minimum class support",
    "G5": "median missingness",
}
BAND_COLORS = {
    "Conditional": "#66a61e",
    "Fragile": "#e6ab02",
    "Not Ready": "#d95f02",
    "No Data": "#7570b3",
}
STATUS_COLORS = {
    "fail": "#c44e00",
    "warn": "#e6ab02",
    "score": "#7a8b8f",
    "nodata": "#7570b3",
}
TEXT_DARK = "#17252a"
TEXT_MUTED = "#52666d"
GRID = "#d8e0e2"


def pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def primary_reason(fails: list[dict], warns: list[dict]) -> tuple[str, str]:
    """Return a one-study-one-reason label and type.

    The backend only applies a gate ceiling, so multiple failed gates can be true.
    This label is intentionally conservative: it preserves multi-gate failures rather
    than pretending one non-G1 failure is always causal.
    """
    if fails:
        fail_ids = {str(g.get("id")) for g in fails}
        if "G1" in fail_ids:
            return "G1 fail: no usable matrix", "nodata"
        if len(fails) > 1:
            return "Multiple hard gate failures", "fail"
        g = fails[0]
        gid = str(g.get("id"))
        return f"{gid} fail: {GATE_SHORT.get(gid, str(g.get('name', 'gate')))}", "fail"
    if warns:
        if len(warns) > 1:
            return "Multiple gate warnings", "warn"
        g = warns[0]
        gid = str(g.get("id"))
        return f"{gid} warn: {GATE_SHORT.get(gid, str(g.get('name', 'gate')))}", "warn"
    return "Score below Ready threshold\n(no gate warning/failure)", "score"


def load_nonready(cache_root: Path) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    idx = json.load((cache_root / "index.json").open())
    rows: list[dict] = []
    primary_counts: Counter[tuple[str, str]] = Counter()
    profile_counts: Counter[tuple[str, ...]] = Counter()
    gate_status = defaultdict(Counter)

    for study_id, payload in sorted(idx["studies"].items()):
        final_band = payload.get("band") or "No Data"
        if final_band == "Ready":
            continue
        state_path = cache_root / payload["state_path"]
        state = json.load(state_path.open())
        rs = state.get("readiness_score", {})
        gates = rs.get("gates", [])
        gate_by_id = {str(g.get("id")): g for g in gates}
        fails = [g for g in gates if g.get("status") == "fail"]
        warns = [g for g in gates if g.get("status") == "warn"]
        primary, primary_type = primary_reason(fails, warns)
        primary_counts[(primary, primary_type)] += 1

        profile = tuple(
            f"{g.get('id')}:{g.get('status')}" for g in gates if g.get("status") != "pass"
        ) or ("No gate warning/failure",)
        profile_counts[profile] += 1

        row = {
            "study_id": study_id,
            "final_band": final_band,
            "study_score": payload.get("score", ""),
            "provisional_band": rs.get("provisional_band", ""),
            "gate_ceiling": rs.get("gate_ceiling", ""),
            "primary_reason": primary.replace("\n", " "),
            "primary_reason_type": primary_type,
            "failed_gates": ";".join(str(g.get("id")) for g in fails),
            "warning_gates": ";".join(str(g.get("id")) for g in warns),
            "workflow_state_path": payload.get("state_path", ""),
        }
        for gid in GATE_ORDER:
            g = gate_by_id.get(gid, {})
            status = str(g.get("status", "missing"))
            gate_status[gid][status] += 1
            row[f"{gid}_status"] = status
            row[f"{gid}_value"] = g.get("value", "")
            row[f"{gid}_summary"] = g.get("summary", "")
        rows.append(row)

    total = len(rows)
    primary_rows = [
        {
            "primary_reason": label.replace("\n", " "),
            "reason_type": typ,
            "count": count,
            "percent_of_nonready_studies": f"{pct(count, total):.6f}",
        }
        for (label, typ), count in primary_counts.most_common()
    ]
    gate_rows = []
    for gid in GATE_ORDER:
        for status in ["fail", "warn", "pass", "missing"]:
            count = gate_status[gid].get(status, 0)
            if count or status != "missing":
                gate_rows.append(
                    {
                        "gate_id": gid,
                        "gate_label": GATE_SHORT[gid],
                        "status": status,
                        "count": count,
                        "percent_of_nonready_studies": f"{pct(count, total):.6f}",
                    }
                )
    profile_rows = [
        {
            "nonpass_gate_profile": ";".join(profile),
            "count": count,
            "percent_of_nonready_studies": f"{pct(count, total):.6f}",
        }
        for profile, count in profile_counts.most_common()
    ]
    return rows, primary_rows, gate_rows, profile_rows


def write_tsv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def make_plot(primary_rows: list[dict], gate_rows: list[dict], out_dir: Path, stem: str) -> None:
    total = sum(int(r["count"]) for r in primary_rows)
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.linewidth": 0.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(10.8, 4.2),
        gridspec_kw={"width_ratios": [1.25, 1.0], "wspace": 0.42},
    )
    fig.patch.set_facecolor("white")

    # Panel A: one primary reason per non-Ready study.
    top = primary_rows[:10]
    labels = [r["primary_reason"] for r in top][::-1]
    counts = [int(r["count"]) for r in top][::-1]
    types = [r["reason_type"] for r in top][::-1]
    y = list(range(len(top)))
    ax1.barh(y, counts, color=[STATUS_COLORS[t] for t in types], edgecolor="white", linewidth=0.7)
    for yi, count in zip(y, counts):
        ax1.text(
            count + max(counts) * 0.015,
            yi,
            f"{count:,} ({pct(count, total):.1f}%)",
            va="center",
            ha="left",
            fontsize=10.5,
            color=TEXT_DARK,
            fontweight="bold",
        )
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=11, fontweight="bold")
    ax1.set_xlabel(
        f"Studies, one primary reason each (n={total:,} non-Ready studies)",
        color=TEXT_DARK,
        fontsize=11.5,
        fontweight="bold",
        labelpad=10,
    )
    ax1.set_title("A  Primary limiting reason", loc="left", fontweight="bold", fontsize=14.5, color=TEXT_DARK, pad=8)
    ax1.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.8)
    ax1.set_axisbelow(True)
    ax1.set_xlim(0, max(counts) * 1.25)

    # Panel B: gate burden counts all non-pass gates, preserving co-occurrence.
    status_lookup = {(r["gate_id"], r["status"]): int(r["count"]) for r in gate_rows}
    y2 = list(range(len(GATE_ORDER)))[::-1]
    fail_counts = [status_lookup.get((gid, "fail"), 0) for gid in GATE_ORDER][::-1]
    warn_counts = [status_lookup.get((gid, "warn"), 0) for gid in GATE_ORDER][::-1]
    gids = GATE_ORDER[::-1]
    ax2.barh(y2, fail_counts, color=STATUS_COLORS["fail"], edgecolor="white", linewidth=0.7, label="Fail")
    ax2.barh(y2, warn_counts, left=fail_counts, color=STATUS_COLORS["warn"], edgecolor="white", linewidth=0.7, label="Warn")
    for yi, gid, fcnt, wcnt in zip(y2, gids, fail_counts, warn_counts):
        if fcnt >= 260:
            ax2.text(fcnt / 2, yi, f"{fcnt:,}", ha="center", va="center", fontsize=10.5, color="white", fontweight="bold")
        if wcnt >= 260:
            ax2.text(fcnt + wcnt / 2, yi, f"{wcnt:,}", ha="center", va="center", fontsize=10.5, color=TEXT_DARK, fontweight="bold")
        if (fcnt + wcnt) < 350:
            if fcnt and wcnt:
                label = f"F={fcnt:,}, W={wcnt:,} ({pct(fcnt+wcnt,total):.1f}%)"
            elif fcnt:
                label = f"F={fcnt:,} ({pct(fcnt+wcnt,total):.1f}%)"
            else:
                label = f"W={wcnt:,} ({pct(fcnt+wcnt,total):.1f}%)"
        else:
            label = f"{pct(fcnt+wcnt,total):.1f}%"
        ax2.text(
            fcnt + wcnt + max([a + b for a, b in zip(fail_counts, warn_counts)]) * 0.02,
            yi,
            label,
            va="center",
            fontsize=10.5,
            color=TEXT_MUTED,
            fontweight="bold",
        )
    ax2.set_yticks(y2)
    ax2.set_yticklabels([GATE_LABELS[gid] for gid in gids], fontsize=11, fontweight="bold")
    ax2.set_xlabel(
        "Studies with non-pass gate status",
        color=TEXT_DARK,
        fontsize=11.5,
        fontweight="bold",
        labelpad=10,
    )
    ax2.set_title("B  Gate burden (co-occurring)", loc="left", fontweight="bold", fontsize=14.5, color=TEXT_DARK, pad=8)
    ax2.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.8)
    ax2.set_axisbelow(True)
    ax2.legend(frameon=False, loc="lower right", fontsize=10, handlelength=1.1, prop={"weight": "bold", "size": 10})

    max_burden = max(f + w for f, w in zip(fail_counts, warn_counts))
    ax2.set_xlim(0, max_burden * 1.18)

    for ax in (ax1, ax2):
        for spine in ["top", "right", "left"]:
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color("#c7d0d3")
        ax.tick_params(axis="x", colors=TEXT_DARK, length=3.5, width=0.75, labelsize=10.5)
        ax.tick_params(axis="y", length=0, colors=TEXT_DARK)
        for tick in ax.get_xticklabels():
            tick.set_fontweight("bold")

    legend_handles = [
        Patch(facecolor=STATUS_COLORS["fail"], label="Hard gate failure"),
        Patch(facecolor=STATUS_COLORS["warn"], label="Gate warning"),
        Patch(facecolor=STATUS_COLORS["score"], label="Score-driven"),
        Patch(facecolor=STATUS_COLORS["nodata"], label="No Data ceiling"),
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.63, 0.965),
        ncol=4,
        frameon=False,
        fontsize=10,
        handlelength=1.1,
        columnspacing=1.4,
        prop={"weight": "bold", "size": 10},
    )
    fig.suptitle("Why studies fall below Ready", x=0.02, y=0.985, ha="left", fontsize=17, fontweight="bold", color=TEXT_DARK)
    fig.text(
        0.01,
        0.0,
        "Panel A assigns one conservative primary reason per non-Ready study. Panel B counts all non-pass gates, so totals can exceed the study denominator.",
        ha="left",
        va="bottom",
        fontsize=8.8,
        color=TEXT_MUTED,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.23, right=0.985, top=0.80, bottom=0.17)
    for ext in ["pdf", "svg"]:
        fig.savefig(out_dir / f"{stem}.{ext}", bbox_inches="tight")
    fig.savefig(out_dir / f"{stem}.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", default="merit-cache-workbench-full-v7")
    parser.add_argument(
        "--out-dir",
        default="merit/manuscript/figures/Figure-5/latest/nonready_gate_failure_reasons_studywise_v7",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, primary_rows, gate_rows, profile_rows = load_nonready(Path(args.cache_root))
    write_tsv(out_dir / "nonready_study_gate_reason_rows.tsv", rows)
    write_tsv(out_dir / "nonready_primary_reason_counts.tsv", primary_rows)
    write_tsv(out_dir / "nonready_gate_status_prevalence.tsv", gate_rows)
    write_tsv(out_dir / "nonready_gate_profile_counts.tsv", profile_rows)
    make_plot(primary_rows, gate_rows, out_dir, "figure5_nonready_gate_failure_reasons_studywise")

    total = len(rows)
    fail_g4 = next(r for r in gate_rows if r["gate_id"] == "G4" and r["status"] == "fail")
    nonpass_g4 = sum(int(r["count"]) for r in gate_rows if r["gate_id"] == "G4" and r["status"] in {"fail", "warn"})
    print(f"Wrote non-Ready gate figure set to {out_dir}")
    print(f"Non-Ready studies: {total}")
    print(f"Top primary reason: {primary_rows[0]['primary_reason']} = {primary_rows[0]['count']} ({float(primary_rows[0]['percent_of_nonready_studies']):.1f}%)")
    print(f"G4 hard failures: {fail_g4['count']} ({float(fail_g4['percent_of_nonready_studies']):.1f}%)")
    print(f"G4 fail or warn: {nonpass_g4} ({pct(nonpass_g4,total):.1f}%)")

    with (out_dir / "README.md").open("w") as fh:
        fh.write("# Non-Ready Gate Failure Reasons, Studywise v7\n\n")
        fh.write(f"Source cache: `{args.cache_root}`\n\n")
        fh.write(f"Scope: study-level records with final band other than Ready (`n={total:,}`).\n\n")
        fh.write("Best visualization: a two-panel summary. Panel A assigns one conservative primary limiting reason per study; Panel B counts all non-pass gates because G2/G4/G3/G5 often co-occur.\n\n")
        fh.write(f"Main result: the dominant hard gate failure is G4 minimum class support: {fail_g4['count']:,}/{total:,} non-Ready studies ({float(fail_g4['percent_of_nonready_studies']):.1f}%). Including warnings, G4 is non-pass in {nonpass_g4:,}/{total:,} studies ({pct(nonpass_g4,total):.1f}%).\n")


if __name__ == "__main__":
    main()
