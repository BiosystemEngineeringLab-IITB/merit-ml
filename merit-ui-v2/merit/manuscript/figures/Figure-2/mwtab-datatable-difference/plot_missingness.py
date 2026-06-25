"""
Plot 4 — Per-analysis paired missingness: datatable vs mwtab.
For the same analysis ID: datatable is always 0% missing (pre-imputed);
mwtab retains raw values and can carry substantial missingness.

Left:  Sorted per-analysis dumbbell chart — each horizontal bar connects
       dt_miss_pct (always 0, left anchor) to mw_miss_pct (right end).
       Only the 1,536 analyses where mwtab has any missing values are shown.
Right: Scatter of mw_miss_pct vs n_mw_feats, coloured by MS/NMR,
       showing whether larger feature sets are more affected.
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype']  = 42

OUT = Path('/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-2/mwtab-datatable-difference')

df = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t').dropna(subset=['mw_miss_pct'])
n_total = len(df)

n_mw_zero = int((df['mw_miss_pct'] == 0).sum())
n_mw_any  = int((df['mw_miss_pct'] >  0).sum())
pct_any   = n_mw_any / n_total * 100
n_high    = int((df['mw_miss_pct'] >= 50).sum())

# Subset: analyses where mwtab has any missing values, sorted ascending
paired = df[df['mw_miss_pct'] > 0].sort_values('mw_miss_pct').reset_index(drop=True)

fig = plt.figure(figsize=(14, 6))
gs  = gridspec.GridSpec(1, 2, wspace=0.30, width_ratios=[1.1, 1])
ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1])

# ── Left: dumbbell chart (horizontal) ─────────────────────────────────────────
y_pos = np.arange(len(paired))

# Horizontal lines from 0 (datatable) to mw_miss_pct
colors_line = np.where(paired['mw_miss_pct'] >= 50, '#c62828', '#FF9800')
for i, (yp, mw_val, col) in enumerate(zip(y_pos, paired['mw_miss_pct'], colors_line)):
    ax1.hlines(yp, 0, mw_val, colors=col, linewidth=0.6, alpha=0.5, zorder=2)

# mwtab endpoint
sc_low  = ax1.scatter(paired.loc[paired['mw_miss_pct'] <  50, 'mw_miss_pct'],
                      y_pos[paired['mw_miss_pct'] <  50],
                      s=8, color='#FF9800', alpha=0.7, linewidths=0, zorder=3)
sc_high = ax1.scatter(paired.loc[paired['mw_miss_pct'] >= 50, 'mw_miss_pct'],
                      y_pos[paired['mw_miss_pct'] >= 50],
                      s=10, color='#c62828', alpha=0.85, linewidths=0, zorder=3)

# datatable anchor at 0 (just a thin line already; optionally add dots)
ax1.scatter(np.zeros(len(paired)), y_pos,
            s=4, color='#1565C0', alpha=0.35, linewidths=0, zorder=3)

ax1.axvline(50, color='#c62828', ls=':', lw=1.5, alpha=0.6)
ax1.set_xlim(-2, 102)
ax1.set_xlabel('Missing value %', fontsize=12, fontweight='bold')
ax1.set_ylabel(f'Analyses with mwtab missingness > 0  (n={n_mw_any:,}, sorted)',
               fontsize=11, fontweight='bold')
ax1.set_title('D4a — Per-analysis missingness: datatable (●, 0%) → mwtab (●)\n'
              f'({n_mw_any:,} of {n_total:,} analyses shown)',
              fontsize=12, fontweight='bold')
ax1.set_yticks([])
ax1.tick_params(labelsize=10)
for t in ax1.get_xticklabels(): t.set_fontweight('bold')
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)
ax1.spines['left'].set_visible(False)
ax1.grid(axis='x', ls='--', alpha=0.3)

# legend / stats
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
legend_els = [
    Line2D([0], [0], marker='o', color='w', markerfacecolor='#1565C0', ms=7,
           label='datatable (always 0%)'),
    Patch(facecolor='#FF9800', alpha=0.8, label=f'mwtab < 50% miss ({(paired["mw_miss_pct"]<50).sum():,})'),
    Patch(facecolor='#c62828', alpha=0.8, label=f'mwtab ≥ 50% miss ({n_high:,})'),
]
ax1.legend(handles=legend_els, fontsize=9, prop={'weight': 'bold'},
           loc='lower right', framealpha=0.9)

ax1.text(0.97, 0.97,
         f'Total analyses: {n_total:,}\n'
         f'mwtab > 0%: {n_mw_any:,} ({pct_any:.1f}%)\n'
         f'mwtab ≥ 50%: {n_high:,} ({n_high/n_total*100:.1f}%)\n'
         f'datatable: always 0%',
         transform=ax1.transAxes, ha='right', va='top', fontsize=9, fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.4', fc='white', ec='#aaa', alpha=0.92))

# ── Right: scatter mw_miss_pct vs n_mw_feats ────────────────────────────────
only_miss = df[df['mw_miss_pct'] > 0]
ms_d  = only_miss[only_miss['is_nmr'] == 0]
nmr_d = only_miss[only_miss['is_nmr'] == 1]

ax2.scatter(ms_d['n_mw_feats'],  ms_d['mw_miss_pct'],
            s=14, alpha=0.4, color='#1565C0', linewidths=0,
            label=f'MS  (n={len(ms_d):,})', zorder=3)
ax2.scatter(nmr_d['n_mw_feats'], nmr_d['mw_miss_pct'],
            s=25, alpha=0.75, color='#c62828', marker='^', linewidths=0.5,
            edgecolors='white', label=f'NMR (n={len(nmr_d):,})', zorder=4)

ax2.axhline(50, color='#c62828', ls=':', lw=1.5, alpha=0.6, label='50% threshold')
ax2.set_xscale('log')
ax2.set_xlim(0.8, df['n_mw_feats'].max() * 1.5)
ax2.set_ylim(-2, 102)
ax2.set_xlabel('Feature count in mwtab (log scale)', fontsize=12, fontweight='bold')
ax2.set_ylabel('mwtab missing value %', fontsize=12, fontweight='bold')
ax2.set_title('D4b — Does feature count predict mwtab missingness?\n'
              '(analyses with any missing values)',
              fontsize=12, fontweight='bold')
ax2.tick_params(labelsize=10)
for t in ax2.get_xticklabels() + ax2.get_yticklabels(): t.set_fontweight('bold')
ax2.legend(fontsize=10, prop={'weight': 'bold'}, loc='upper right', framealpha=0.9)
ax2.spines['top'].set_visible(False); ax2.spines['right'].set_visible(False)
ax2.grid(ls='--', alpha=0.3)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot4_missingness.{ext}', dpi=300, bbox_inches='tight')
print(f'Plot 4 saved.  paired view: {n_mw_any} analyses, dt=0% → mwtab up to {df["mw_miss_pct"].max():.1f}%')
