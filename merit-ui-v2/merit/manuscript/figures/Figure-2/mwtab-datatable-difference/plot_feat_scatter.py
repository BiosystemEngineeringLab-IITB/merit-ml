"""
Plot 1 — Feature count scatter: datatable vs mwtab (log-log, identity line)
4,464 combo-110 analyses (mwtab + datatable valid, no untarg).
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype']  = 42

OUT = Path('/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-2/mwtab-datatable-difference')

df = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t')
# keep rows where both > 0 (log-safe)
d = df[(df['n_dt_feats'] > 0) & (df['n_mw_feats'] > 0)].copy()

nmr  = d[d['is_nmr'] == 1]
ms   = d[d['is_nmr'] == 0]

fig, ax = plt.subplots(figsize=(7, 6.5))

ax.scatter(ms['n_dt_feats'],  ms['n_mw_feats'],
           s=14, alpha=0.35, color='#1565C0', linewidths=0, label=f'MS  (n={len(ms):,})', zorder=3)
ax.scatter(nmr['n_dt_feats'], nmr['n_mw_feats'],
           s=25, alpha=0.75, color='#c62828', marker='^', linewidths=0.4,
           edgecolors='white', label=f'NMR (n={len(nmr):,})', zorder=4)

# identity line
lim = max(d['n_dt_feats'].max(), d['n_mw_feats'].max()) * 1.5
ax.plot([1, lim], [1, lim], ls='--', lw=1.4, color='#555', alpha=0.6, zorder=2, label='Identity (y = x)')

# 2× lines
ax.plot([1, lim], [2, 2*lim], ls=':', lw=1.0, color='#888', alpha=0.45, zorder=2, label='mwtab 2× datatable')

ax.set_xscale('log'); ax.set_yscale('log')
ax.set_xlim(0.8, lim); ax.set_ylim(0.8, lim)

ax.set_xlabel('Feature count — datatable', fontsize=12, fontweight='bold')
ax.set_ylabel('Feature count — mwtab', fontsize=12, fontweight='bold')
ax.set_title('D1 — Feature count: datatable vs mwtab\n(4,464 combo-110 analyses)',
             fontsize=14, fontweight='bold')
ax.tick_params(labelsize=11)
for t in ax.get_xticklabels() + ax.get_yticklabels():
    t.set_fontweight('bold')

# summary stats
med_dt = int(d['n_dt_feats'].median())
med_mw = int(d['n_mw_feats'].median())
n_mw_gt = int((d['n_mw_feats'] > d['n_dt_feats']).sum())
pct = n_mw_gt / len(d) * 100
ax.text(0.97, 0.05,
        f'Median dt: {med_dt}  |  Median mwtab: {med_mw}\n'
        f'mwtab > datatable: {n_mw_gt:,}/{len(d):,} ({pct:.1f}%)',
        transform=ax.transAxes, ha='right', va='bottom', fontsize=9.5, fontweight='bold',
        bbox=dict(boxstyle='round,pad=0.4', fc='white', ec='#aaa', alpha=0.9))

ax.legend(fontsize=10, prop={'weight': 'bold'}, loc='upper left', framealpha=0.9)
ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
ax.grid(True, ls='--', alpha=0.3)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot1_feat_count_scatter.{ext}', dpi=300, bbox_inches='tight')
print('Plot 1 saved.')
