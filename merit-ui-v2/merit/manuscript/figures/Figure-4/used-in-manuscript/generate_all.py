#!/usr/bin/env python3
"""
Master script — generates all Figure-4 used-in-manuscript plots:

  Standalone constituents (also embedded in combined):
    A  feature_violin.{pdf,png,svg}
    B  sample_violin.{pdf,png,svg}
    C  feature_type_violin.{pdf,png,svg}
    D  pn_ratio_violin.{pdf,png,svg}        ← main (distribution)
    E  data_scale_violin.{pdf,png,svg}
    F  missingness_combined.{pdf,png,svg}   ← 3-panel histogram

  Supplementary:
    supp_pn_scatter.{pdf,png,svg}           ← scatter panel from figure2d

  Combined panel:
    figure4_combined.{pdf,png,svg}
"""
from __future__ import annotations
import json, math, warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from scipy.stats import gaussian_kde
from statistics import median as pymedian
from pathlib import Path

warnings.filterwarnings('ignore')

plt.rcParams['font.family']  = 'DejaVu Sans'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype']  = 42

OUT = Path('/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-4/used-in-manuscript')
OUT.mkdir(exist_ok=True)

# ── palette (consistent across all panels) ───────────────────────────────────
C_DT  = '#5b9bd5'   # steel blue  — datatable
C_MW  = '#70ad47'   # muted green — mwtab
C_UT  = '#e8a945'   # amber       — untarg
COLORS = {'Datatable': C_DT, 'mwTab': C_MW, 'Untarg': C_UT}

# ── load data — all three sources INDEPENDENT (no pairing) ───────────────────
# Each source uses all analyses where that source is valid:
#   Datatable : n=4,872   mwTab : n=4,990   Untarg : n=1,887
feat_raw   = json.load(open('/tmp/feat_counts_raw.json'))
samp_raw   = json.load(open('/tmp/sample_counts.json'))
feat_types = json.load(open('/tmp/feature_type_counts.json'))

dt_ind = pd.read_csv('/tmp/dt_independent.tsv', sep='\t')
mw_ind = pd.read_csv('/tmp/mw_independent.tsv', sep='\t')
ut_ind = pd.read_csv('/tmp/ut_independent.tsv', sep='\t')

# Feature counts (panels A, C) — from independent collection
dt_feats = [r['n'] for r in feat_raw['dt'] if r['n'] > 0]
mw_feats = [r['n'] for r in feat_raw['mw'] if r['n'] > 0]
ut_feats = [r['n'] for r in feat_raw['ut'] if r['n'] > 0]

# Sample counts (panel B) — from independent collection
dt_samps = samp_raw['dt_filt']
mw_samps = samp_raw['mw_filt']
ut_samps = samp_raw['ut_filt']

# P/N ratios (panel D, G) — per-analysis from independent TSVs
dt_pn = (dt_ind['n_feats'] / dt_ind['n_samples'].replace(0, np.nan)).dropna().tolist()
mw_pn = (mw_ind['n_feats'] / mw_ind['n_samples'].replace(0, np.nan)).dropna().tolist()
ut_pn = (ut_ind['n_feats'] / ut_ind['n_samples'].replace(0, np.nan)).dropna().tolist()

# Intensity (panel E) — per-analysis median from independent TSVs
dt_int = [math.log10(v) for v in dt_ind['median_intensity'].dropna() if v > 0]
mw_int = [math.log10(v) for v in mw_ind['median_intensity'].dropna() if v > 0]
ut_int = [math.log10(v) for v in ut_ind['median_intensity'].dropna() if v > 0]

# Missingness (panel F) — from independent TSVs
dt_miss_vals = dt_ind['miss_pct'].dropna().values
mw_miss_vals = mw_ind['miss_pct'].dropna().values
ut_miss_vals = ut_ind['miss_pct'].dropna().values

# Scatter (panel G) — independent per-analysis n_feats vs n_samples
# (use dt_ind / mw_ind / ut_ind directly in draw block)


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED DRAWING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def log_violin(ax, data, pos, color, half_width=0.34, alpha=0.75, bw=0.25):
    vals = np.array([x for x in data if x > 0 and np.isfinite(x)], dtype=float)
    if len(vals) < 5:
        return
    log_v = np.log10(vals)
    kde = gaussian_kde(log_v, bw_method=bw)
    y_log = np.linspace(log_v.min(), log_v.max(), 400)
    dens = kde(y_log)
    dens = dens / dens.max() * half_width
    y_lin = 10 ** y_log
    ax.fill_betweenx(y_lin, pos - dens, pos + dens, color=color, alpha=alpha, lw=0)
    ax.plot(pos - dens, y_lin, color=color, lw=0.6, alpha=0.6)
    ax.plot(pos + dens, y_lin, color=color, lw=0.6, alpha=0.6)


def log_violin_linear_data(ax, data, pos, color, half_width=0.34, alpha=0.75, bw=0.25):
    """For data already in log10 space — plot on LINEAR axis."""
    vals = np.array([x for x in data if np.isfinite(x)], dtype=float)
    if len(vals) < 5:
        return
    kde = gaussian_kde(vals, bw_method=bw)
    y_grid = np.linspace(vals.min(), vals.max(), 400)
    dens = kde(y_grid)
    dens = dens / dens.max() * half_width
    ax.fill_betweenx(y_grid, pos - dens, pos + dens, color=color, alpha=alpha, lw=0)
    ax.plot(pos - dens, y_grid, color=color, lw=0.6, alpha=0.6)
    ax.plot(pos + dens, y_grid, color=color, lw=0.6, alpha=0.6)


def embed_box(ax, data, pos, log_scale=True, box_w=0.11):
    vals = [x for x in data if x > 0 and np.isfinite(x)]
    if len(vals) < 5:
        return None
    q1, med, q3 = np.percentile(vals, [25, 50, 75])
    iqr = q3 - q1
    lo = max(min(vals), q1 - 1.5 * iqr)
    hi = min(max(vals), q3 + 1.5 * iqr)
    rect = plt.Rectangle((pos - box_w/2, q1), box_w, q3 - q1,
                          facecolor='white', edgecolor='#333', lw=1.3, zorder=5)
    ax.add_patch(rect)
    ax.plot([pos - box_w/2, pos + box_w/2], [med, med], color='#111', lw=2.2, zorder=6)
    ax.plot([pos, pos], [lo, q1], color='#444', lw=1.2, zorder=4)
    ax.plot([pos, pos], [q3, hi], color='#444', lw=1.2, zorder=4)
    for y in (lo, hi):
        ax.plot([pos - 0.045, pos + 0.045], [y, y], color='#444', lw=1.2, zorder=4)
    return med


def embed_box_linear(ax, data, pos, box_w=0.11):
    """Boxplot for data on linear axis (e.g. log10-transformed intensity)."""
    vals = [x for x in data if np.isfinite(x)]
    if len(vals) < 5:
        return None
    q1, med, q3 = np.percentile(vals, [25, 50, 75])
    iqr = q3 - q1
    lo = max(min(vals), q1 - 1.5 * iqr)
    hi = min(max(vals), q3 + 1.5 * iqr)
    rect = plt.Rectangle((pos - box_w/2, q1), box_w, q3 - q1,
                          facecolor='white', edgecolor='#333', lw=1.3, zorder=5)
    ax.add_patch(rect)
    ax.plot([pos - box_w/2, pos + box_w/2], [med, med], color='#111', lw=2.2, zorder=6)
    ax.plot([pos, pos], [lo, q1], color='#444', lw=1.2, zorder=4)
    ax.plot([pos, pos], [q3, hi], color='#444', lw=1.2, zorder=4)
    for y in (lo, hi):
        ax.plot([pos - 0.045, pos + 0.045], [y, y], color='#444', lw=1.2, zorder=4)
    return med


def style_log_violin_ax(ax, ylabel, ylim=(0.8, None), ymax_auto=None):
    ax.set_yscale('log')
    ymax = ymax_auto if ymax_auto else ylim[1]
    ax.set_ylim(ylim[0], ymax)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f'{int(x):,}' if x >= 1 else f'{x:.2f}'))
    ax.yaxis.grid(True, which='major', ls='--', lw=0.6, color='#ccc', zorder=0)
    ax.set_axisbelow(True)
    ax.set_ylabel(ylabel, fontsize=11, fontweight='bold')
    ax.tick_params(labelsize=10, width=1.1)
    for t in ax.get_yticklabels(): t.set_fontweight('bold')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)


def set_source_xticks(ax, positions, labels):
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=11, fontweight='bold')


def add_panel_label(ax, letter):
    ax.text(-0.08, 1.04, letter, transform=ax.transAxes,
            fontsize=14, fontweight='bold', va='bottom', ha='left')


# ═══════════════════════════════════════════════════════════════════════════════
# PANEL DRAWING FUNCTIONS  (each returns the ax it drew into)
# ═══════════════════════════════════════════════════════════════════════════════

def draw_feature_violin(ax, letter=None):
    grps = [('Datatable', dt_feats, C_DT, 1),
            ('mwTab',     mw_feats, C_MW, 2),
            ('Untarg',    ut_feats, C_UT, 3)]
    for lbl, data, col, pos in grps:
        log_violin(ax, data, pos, col)
        med = embed_box(ax, data, pos)
        ymax = np.percentile([x for x in data if x > 0], 99) * 2.0
        ax.text(pos, ymax, f'med={int(med):,}', ha='center', va='bottom',
                fontsize=9, fontweight='bold', color='#222')
    style_log_violin_ax(ax, 'Feature count (raw)', ylim=(0.8, 2e5))
    ax.set_xlim(0.4, 3.6)
    set_source_xticks(ax, [1,2,3], ['Datatable','mwTab','Untarg'])
    for lbl, data, col, pos in grps:
        ax.text(pos, 0.97, f'n={len([x for x in data if x>0]):,}',
                ha='center', va='top', fontsize=8, color='#555',
                transform=ax.get_xaxis_transform())
    ax.set_title('Feature count\nper analysis', fontsize=11, fontweight='bold', pad=4)
    if letter: add_panel_label(ax, letter)
    return ax


def draw_sample_violin(ax, letter=None):
    grps = [('Datatable', dt_samps, C_DT, 1),
            ('mwTab',     mw_samps, C_MW, 2),
            ('Untarg',    ut_samps, C_UT, 3)]
    for lbl, data, col, pos in grps:
        log_violin(ax, data, pos, col)
        med = embed_box(ax, data, pos)
        ymax = np.percentile([x for x in data if x > 0], 99) * 1.8
        ax.text(pos, ymax, f'med={int(med):,}', ha='center', va='bottom',
                fontsize=9, fontweight='bold', color='#222')
    style_log_violin_ax(ax, 'Sample count', ylim=(0.8, 2e4))
    ax.set_xlim(0.4, 3.6)
    set_source_xticks(ax, [1,2,3], ['Datatable','mwTab','Untarg'])
    for lbl, data, col, pos in grps:
        ax.text(pos, 0.97, f'n={len([x for x in data if x>0]):,}',
                ha='center', va='top', fontsize=8, color='#555',
                transform=ax.get_xaxis_transform())
    ax.set_title('Sample count\nper analysis', fontsize=11, fontweight='bold', pad=4)
    if letter: add_panel_label(ax, letter)
    return ax


def draw_feature_type_violin(ax, letter=None):
    C_NAMED = {'dt': '#4472C4', 'mw': '#70AD47', 'ut': '#E67E22'}
    C_MZRT  = '#AAAAAA'
    GAP_INNER, GAP_OUTER = 0.36, 1.0
    positions = {}
    center = 0
    for src in ['dt', 'mw', 'ut']:
        positions[(src, 'named')] = center - GAP_INNER / 2
        positions[(src, 'mzrt')]  = center + GAP_INNER / 2
        center += GAP_OUTER
    MAX_N = 4990

    def _violin(data, pos, color, width=0.13):
        data = [x for x in data if x > 0]
        if len(data) < 5: return
        sw = max(width * np.sqrt(len(data) / MAX_N), 0.025)
        log_d = np.log10(data)
        kde = gaussian_kde(log_d, bw_method=0.30)
        y_log = np.linspace(log_d.min(), log_d.max(), 400)
        dens = kde(y_log); dens = dens / dens.max() * sw
        y_orig = 10 ** y_log
        ax.fill_betweenx(y_orig, pos - dens, pos + dens, color=color, alpha=0.75, lw=0, zorder=3)
        ax.plot(pos - dens, y_orig, color=color, lw=0.5, alpha=0.6)
        ax.plot(pos + dens, y_orig, color=color, lw=0.5, alpha=0.6)

    def _box(data, pos):
        data = [x for x in data if x > 0]
        if len(data) < 5: return None
        q1, med, q3 = np.percentile(data, [25,50,75])
        iqr = q3 - q1
        lo = max(min(data), q1 - 1.5*iqr); hi = min(max(data), q3 + 1.5*iqr)
        bw = 0.06
        rect = plt.Rectangle((pos-bw/2, q1), bw, q3-q1,
                              facecolor='white', edgecolor='#333', lw=1.2, zorder=5)
        ax.add_patch(rect)
        ax.plot([pos-bw/2, pos+bw/2], [med, med], color='#111', lw=2.0, zorder=6)
        ax.plot([pos, pos], [lo, q1], color='#444', lw=1.0, zorder=4)
        ax.plot([pos, pos], [q3, hi], color='#444', lw=1.0, zorder=4)
        for y in (lo, hi): ax.plot([pos-0.04, pos+0.04], [y, y], color='#444', lw=1.0, zorder=4)
        return int(med)

    for src in ['dt', 'mw', 'ut']:
        records = feat_types[src]
        named = [r['named'] for r in records]
        mzrt  = [r['mzrt']  for r in records]
        pn = positions[(src, 'named')]; pm = positions[(src, 'mzrt')]
        _violin(named, pn, C_NAMED[src]); med_n = _box(named, pn)
        _violin(mzrt,  pm, C_MZRT);       med_m = _box(mzrt, pm)
        nn = [x for x in named if x > 0]; nm = [x for x in mzrt if x > 0]
        ax.text(pn - 0.03, 0.975, f'n={len(nn):,}', ha='right', va='top',
                fontsize=7.5, fontweight='bold', color='#444', transform=ax.get_xaxis_transform())
        if nm:
            ax.text(pm + 0.03, 0.975, f'n={len(nm):,}', ha='left', va='top',
                    fontsize=7.5, fontweight='bold', color='#777', transform=ax.get_xaxis_transform())
        if nn:
            ax.text(pn - 0.03, 0.925, f'med={int(pymedian(nn)):,}', ha='right', va='top',
                    fontsize=8, fontweight='bold', color=C_NAMED[src], transform=ax.get_xaxis_transform())
        if nm:
            ax.text(pm + 0.03, 0.925, f'med={int(pymedian(nm)):,}', ha='left', va='top',
                    fontsize=8, fontweight='bold', color='#666', transform=ax.get_xaxis_transform())

    src_centers = [(positions[(s,'named')] + positions[(s,'mzrt')]) / 2 for s in ['dt','mw','ut']]
    ax.set_xticks(src_centers)
    ax.set_xticklabels(['Datatable','mwTab','Untarg'], fontsize=11, fontweight='bold')
    ax.set_xlim(positions[('dt','named')] - 0.28, positions[('ut','mzrt')] + 0.28)
    ax.set_yscale('log'); ax.set_ylim(0.8, 3e5)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f'{int(x):,}' if x >= 1 else f'{x:.1f}'))
    ax.yaxis.grid(True, which='major', ls='--', lw=0.6, color='#ccc', zorder=0)
    ax.set_axisbelow(True)
    ax.set_ylabel('Feature count (log scale)', fontsize=11, fontweight='bold')
    ax.tick_params(labelsize=10)
    for t in ax.get_yticklabels(): t.set_fontweight('bold')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    legend_handles = [
        mpatches.Patch(color='#4472C4', alpha=0.75, label='Named — Datatable'),
        mpatches.Patch(color='#70AD47', alpha=0.75, label='Named — mwTab'),
        mpatches.Patch(color='#E67E22', alpha=0.75, label='Named — Untarg'),
        mpatches.Patch(color='#AAAAAA', alpha=0.75, label='Unannotated (mz/RT, NMR, IDs)'),
    ]
    ax.legend(handles=legend_handles, fontsize=8, prop={'weight':'bold'},
              loc='lower right', framealpha=0.92)
    ax.set_title('Feature identity\ncomposition', fontsize=11, fontweight='bold', pad=4)
    if letter: add_panel_label(ax, letter)
    return ax


def draw_pn_violin(ax, letter=None):
    grps = [('Datatable', dt_pn, C_DT, 1),
            ('mwTab',     mw_pn, C_MW, 2),
            ('Untarg',    ut_pn, C_UT, 3)]
    for lbl, data, col, pos in grps:
        log_violin(ax, data, pos, col)
        med = embed_box(ax, data, pos)
        ymax = np.percentile([x for x in data if x > 0], 99) * 2.0
        ax.text(pos, ymax, f'med={med:.1f}', ha='center', va='bottom',
                fontsize=9, fontweight='bold', color='#222')
    # p=n and p=10n reference lines
    ax.axhline(1,  color='#333', ls='--', lw=1.2, alpha=0.7, zorder=2)
    ax.axhline(10, color='#333', ls=':',  lw=1.2, alpha=0.5, zorder=2)
    ax.text(3.55, 1.05,  'p = n',   va='bottom', ha='right', fontsize=8,
            fontweight='bold', color='#333', alpha=0.8)
    ax.text(3.55, 10.5, 'p = 10n', va='bottom', ha='right', fontsize=8,
            fontweight='bold', color='#333', alpha=0.6)
    style_log_violin_ax(ax, 'Feature / sample ratio (p/n)', ylim=(0.005, 5e3))
    ax.set_xlim(0.4, 3.6)
    set_source_xticks(ax, [1,2,3], ['Datatable','mwTab','Untarg'])
    ax.set_title('Feature-to-sample\nratio (p/n)', fontsize=11, fontweight='bold', pad=4)
    if letter: add_panel_label(ax, letter)
    return ax


def draw_data_scale(ax, letter=None):
    all_int = dt_int + mw_int + ut_int
    ymin = math.floor(min(all_int)) - 1
    ymax_lim = math.ceil(max(all_int)) + 1
    grps = [('Datatable', dt_int, C_DT, 1),
            ('mwTab',     mw_int, C_MW, 2),
            ('Untarg',    ut_int, C_UT, 3)]
    for lbl, data, col, pos in grps:
        log_violin_linear_data(ax, data, pos, col)
        med = embed_box_linear(ax, data, pos)
        ann_y = max(data) + 0.25
        ax.text(pos, ann_y, f'10$^{{{med:.1f}}}$', ha='center', va='bottom',
                fontsize=9, fontweight='bold', color='#222')
    ax.set_ylabel('Median intensity (log₁₀ scale)', fontsize=11, fontweight='bold')
    # explicit integer ticks so labels are always correct
    yticks = list(range(ymin, ymax_lim + 1))
    ax.set_ylim(ymin, ymax_lim)
    ax.set_yticks(yticks)
    ax.set_yticklabels([f'$10^{{{y}}}$' for y in yticks], fontsize=9)
    for t in ax.get_yticklabels(): t.set_fontweight('bold')
    ax.yaxis.grid(True, which='major', ls='--', lw=0.6, color='#ccc', zorder=0)
    ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.tick_params(labelsize=9)
    ax.set_xlim(0.4, 3.6)
    set_source_xticks(ax, [1,2,3], ['Datatable','mwTab','Untarg'])
    ax.set_title('Measurement scale\n(per-analysis median intensity)', fontsize=11, fontweight='bold', pad=4)
    for lbl, data, col, pos in grps:
        ax.text(pos, 0.97, f'n={len(data):,}', ha='center', va='top',
                fontsize=8, color='#555', transform=ax.get_xaxis_transform())
    if letter: add_panel_label(ax, letter)
    return ax


def draw_missingness_panel(ax0, ax1, ax2, letter=None):
    sources = [
        ('Datatable', dt_miss_vals, '#1565C0', len(dt_miss_vals)),
        ('mwTab',     mw_miss_vals, '#FF9800', len(mw_miss_vals)),
        ('Untarg',    ut_miss_vals, C_UT,     len(ut_miss_vals)),
    ]
    bins = np.linspace(0, 100, 51)
    for ax, (lbl, vals, col, n) in zip([ax0, ax1, ax2], sources):
        med   = float(np.median(vals))
        mean  = float(np.mean(vals))
        n_any = int(np.sum(vals > 0))
        n_50  = int(np.sum(vals >= 50))
        ax.hist(vals, bins=bins, color=col, alpha=0.85, edgecolor='white', lw=0.4)
        ax.axvline(med, color='#c62828', lw=1.8, ls='-',  label=f'Median {med:.1f}%')
        ax.axvline(50,  color='#333',    lw=1.1, ls=':',  alpha=0.6, label='50%')
        ax.set_xlabel('Missing value %', fontsize=10, fontweight='bold')
        ax.set_ylabel('Analyses', fontsize=10, fontweight='bold')
        ax.set_title(f'{lbl}  (n={n:,})', fontsize=11, fontweight='bold', pad=3)
        ax.set_xlim(-2, 102)
        ax.tick_params(labelsize=9)
        for t in ax.get_xticklabels() + ax.get_yticklabels(): t.set_fontweight('bold')
        ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
        ax.grid(axis='y', ls='--', alpha=0.3)
        ax.legend(fontsize=8, prop={'weight':'bold'}, framealpha=0.9)
        stats = (f'> 0%: {n_any:,} ({n_any/n*100:.0f}%)\n'
                 f'≥ 50%: {n_50:,} ({n_50/n*100:.1f}%)\n'
                 f'Mean: {mean:.1f}%')
        ax.text(0.97, 0.97, stats, transform=ax.transAxes, ha='right', va='top',
                fontsize=8, fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#aaa', alpha=0.9))
    if letter:
        ax0.text(-0.12, 1.04, letter, transform=ax0.transAxes,
                 fontsize=14, fontweight='bold', va='bottom', ha='left')


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE CONSTITUENT PLOTS
# ═══════════════════════════════════════════════════════════════════════════════

def save(fig, name):
    for ext in ('pdf', 'png', 'svg'):
        fig.savefig(OUT / f'{name}.{ext}', dpi=300, bbox_inches='tight')
    print(f'  Saved: {name}')


print('Generating standalone constituent plots...')

fig, ax = plt.subplots(figsize=(6, 5.5)); draw_feature_violin(ax)
fig.tight_layout(); save(fig, 'A_feature_violin'); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 5.5)); draw_sample_violin(ax)
fig.tight_layout(); save(fig, 'B_sample_violin'); plt.close(fig)

fig, ax = plt.subplots(figsize=(8.5, 5.5)); draw_feature_type_violin(ax)
fig.tight_layout(); save(fig, 'C_feature_type_violin'); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 5.5)); draw_pn_violin(ax)
fig.tight_layout(); save(fig, 'D_pn_ratio_violin'); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 5.5)); draw_data_scale(ax)
fig.tight_layout(); save(fig, 'E_data_scale_violin'); plt.close(fig)

fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
draw_missingness_panel(axes[0], axes[1], axes[2])
fig.suptitle('Per-analysis missingness across MW tabular sources',
             fontsize=12, fontweight='bold', y=1.02)
fig.tight_layout(); save(fig, 'F_missingness_combined'); plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════════
# SUPPLEMENTARY — p/n scatter (left panel of original figure2d)
# ═══════════════════════════════════════════════════════════════════════════════

print('Generating supplementary p/n scatter...')

fig, ax = plt.subplots(figsize=(6, 5.5))
src_groups = [
    ('Datatable', dt_ind['n_samples'], dt_ind['n_feats'], C_DT),
    ('mwTab',     mw_ind['n_samples'], mw_ind['n_feats'], C_MW),
    ('Untarg',    ut_ind['n_samples'], ut_ind['n_feats'], C_UT),
]
for lbl, x, y, col in src_groups:
    ax.scatter(x, y, s=8, alpha=0.35, color=col, linewidths=0, label=lbl, zorder=3)

lims = [0.8, 1.1e5]
ax.set_xlim(*lims); ax.set_ylim(*lims)
ax.set_xscale('log'); ax.set_yscale('log')
x_line = np.logspace(0, 5, 100)
ax.plot(x_line, x_line,      color='#333', ls='--', lw=1.2, alpha=0.7, label='p = n')
ax.plot(x_line, x_line * 10, color='#333', ls=':',  lw=1.2, alpha=0.5, label='p = 10n')
ax.set_xlabel('Sample count (n)', fontsize=12, fontweight='bold')
ax.set_ylabel('Feature count (p)', fontsize=12, fontweight='bold')
ax.set_title('Samples vs features\n(all valid analyses)', fontsize=12, fontweight='bold')
ax.legend(fontsize=9, prop={'weight':'bold'}, framealpha=0.9)
ax.tick_params(labelsize=10)
for t in ax.get_xticklabels() + ax.get_yticklabels(): t.set_fontweight('bold')
ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
ax.grid(ls='--', alpha=0.25, zorder=0)
fig.tight_layout()
save(fig, 'supp_pn_scatter'); plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════════
# COMBINED PANEL  figure4_combined
#
# Layout  (GridSpec 3 rows × 3 cols, height_ratios [1.3, 1.3, 1.0])
#   Row 0:  A feature violin  | B sample violin | C feature type violin
#   Row 1:  D p/n violin      | E data scale    | (empty → used for row 1 right)
#   Row 2:  F missingness × 3 panels (spans all 3 cols)
# ═══════════════════════════════════════════════════════════════════════════════

print('Generating combined panel figure...')

fig = plt.figure(figsize=(18, 16))
gs  = gridspec.GridSpec(3, 3,
                        height_ratios=[1.35, 1.35, 1.0],
                        hspace=0.52, wspace=0.38)

axA = fig.add_subplot(gs[0, 0])
axB = fig.add_subplot(gs[0, 1])
axC = fig.add_subplot(gs[0, 2])
axD = fig.add_subplot(gs[1, 0])
axE = fig.add_subplot(gs[1, 1])

# Row 2: missingness — use nested GridSpec inside gs[2, :]
gs_miss = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs[2, :],
                                           wspace=0.32)
axF0 = fig.add_subplot(gs_miss[0])
axF1 = fig.add_subplot(gs_miss[1])
axF2 = fig.add_subplot(gs_miss[2])

axS = fig.add_subplot(gs[1, 2])

draw_feature_violin(axA,   letter='A')
draw_sample_violin(axB,    letter='B')
draw_feature_type_violin(axC, letter='C')
draw_pn_violin(axD,        letter='D')
draw_data_scale(axE,       letter='E')

# gs[1, 2] — supplementary p/n scatter
for lbl, x, y, col in [
    ('Datatable', dt_ind['n_samples'], dt_ind['n_feats'], C_DT),
    ('mwTab',     mw_ind['n_samples'], mw_ind['n_feats'], C_MW),
    ('Untarg',    ut_ind['n_samples'], ut_ind['n_feats'], C_UT),
]:
    axS.scatter(x, y, s=6, alpha=0.30, color=col, linewidths=0, label=lbl, zorder=3)
_lims = [0.8, 1.1e5]
axS.set_xlim(*_lims); axS.set_ylim(*_lims)
axS.set_xscale('log'); axS.set_yscale('log')
_x = np.logspace(0, 5, 100)
axS.plot(_x, _x,      color='#333', ls='--', lw=1.2, alpha=0.7, label='p = n')
axS.plot(_x, _x * 10, color='#333', ls=':',  lw=1.2, alpha=0.5, label='p = 10n')
axS.set_xlabel('Sample count (n)', fontsize=10, fontweight='bold')
axS.set_ylabel('Feature count (p)', fontsize=10, fontweight='bold')
axS.set_title('Samples vs features\n(supplementary)', fontsize=11, fontweight='bold', pad=4)
axS.legend(fontsize=8, prop={'weight': 'bold'}, framealpha=0.9)
axS.tick_params(labelsize=9)
for t in axS.get_xticklabels() + axS.get_yticklabels(): t.set_fontweight('bold')
axS.spines['top'].set_visible(False); axS.spines['right'].set_visible(False)
axS.grid(ls='--', alpha=0.25, zorder=0)
add_panel_label(axS, 'G')

draw_missingness_panel(axF0, axF1, axF2, letter='F')

fig.suptitle('ML-readiness characteristics of Metabolomics Workbench tabular sources\n'
             '(per-analysis distributions across Datatable, mwTab, and Untarg)',
             fontsize=14, fontweight='bold', y=0.995)

for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'figure4_combined.{ext}', dpi=300, bbox_inches='tight')
    print(f'  Saved: figure4_combined.{ext}')

plt.close(fig)
print('\nAll done.')
