"""
Combined missingness plot — all three MW tabular sources.
Three histogram panels on a shared x-axis (0–100%).
datatable: spike at 0 (structural completeness).
mwtab / untarg: right-skewed distributions with tails.
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

# ── Load data ────────────────────────────────────────────────────────────────
mw = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t').dropna(subset=['mw_miss_pct'])
ut = pd.read_csv('/tmp/untarg_missingness.tsv', sep='\t').dropna(subset=['miss_pct'])

dt_vals = mw['dt_miss_pct'].values          # all 0.0 by construction
mw_vals = mw['mw_miss_pct'].values
ut_vals = ut['miss_pct'].values

sources = [
    ('Datatable',  dt_vals, '#1565C0', 4464),
    ('mwTab',      mw_vals, '#FF9800', len(mw_vals)),
    ('Untarg',     ut_vals, '#5C6BC0', len(ut_vals)),
]

bins = np.linspace(0, 100, 51)   # 2% bins

fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=False)

for ax, (label, vals, color, n) in zip(axes, sources):
    med  = float(np.median(vals))
    mean = float(np.mean(vals))
    n_any  = int(np.sum(vals > 0))
    n_50   = int(np.sum(vals >= 50))

    ax.hist(vals, bins=bins, color=color, alpha=0.85,
            edgecolor='white', linewidth=0.4)
    ax.axvline(med, color='#c62828', lw=2, ls='-',  label=f'Median {med:.1f}%')
    ax.axvline(50,  color='#333333', lw=1.2, ls=':', alpha=0.6, label='50% threshold')

    ax.set_xlabel('Missing value %', fontsize=12, fontweight='bold')
    ax.set_ylabel('Number of analyses', fontsize=12, fontweight='bold')
    ax.set_title(f'{label}\n(n={n:,})', fontsize=13, fontweight='bold')
    ax.set_xlim(-2, 102)
    ax.tick_params(labelsize=10)
    for t in ax.get_xticklabels() + ax.get_yticklabels():
        t.set_fontweight('bold')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', ls='--', alpha=0.3)
    ax.legend(fontsize=9, prop={'weight': 'bold'}, framealpha=0.9)

    # Stats box
    stats_txt = (
        f'miss = 0%: {n - n_any:,} ({(n - n_any)/n*100:.1f}%)\n'
        f'miss > 0%: {n_any:,} ({n_any/n*100:.1f}%)\n'
        f'miss ≥ 50%: {n_50:,} ({n_50/n*100:.1f}%)\n'
        f'Mean: {mean:.1f}%'
    )
    ax.text(0.97, 0.97, stats_txt,
            transform=ax.transAxes, ha='right', va='top',
            fontsize=9, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.4', fc='white', ec='#aaa', alpha=0.92))

fig.suptitle('Per-analysis missingness across Metabolomics Workbench tabular sources',
             fontsize=13, fontweight='bold', y=1.02)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot_missingness_combined.{ext}', dpi=300, bbox_inches='tight')
print('Saved: plot_missingness_combined.{pdf,png,svg}')
