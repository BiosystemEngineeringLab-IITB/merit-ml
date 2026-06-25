"""
Plot — Per-analysis missingness distribution for untarg_data source.
Left:  Histogram of miss_pct distribution across all 1,885 analyses.
Right: Scatter of miss_pct vs n_feats (feature count).
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

df = pd.read_csv('/tmp/untarg_missingness.tsv', sep='\t').dropna(subset=['miss_pct'])
n_total   = len(df)
n_zero    = int((df['miss_pct'] == 0).sum())
n_any     = int((df['miss_pct'] >  0).sum())
n_50      = int((df['miss_pct'] >= 50).sum())
n_80      = int((df['miss_pct'] >= 80).sum())
med       = df['miss_pct'].median()
mean      = df['miss_pct'].mean()

fig = plt.figure(figsize=(14, 6))
gs  = gridspec.GridSpec(1, 2, wspace=0.30)
ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1])

# ── Left: histogram ─────────────────────────────────────────────────────────
bins = np.linspace(0, 100, 51)
ax1.hist(df['miss_pct'], bins=bins, color='#5C6BC0', alpha=0.85, edgecolor='white', linewidth=0.4)
ax1.axvline(med,  color='#c62828', lw=2,   ls='-',  label=f'Median {med:.1f}%')
ax1.axvline(50,   color='#FF9800', lw=1.5, ls='--', label='50% threshold')

ax1.set_xlabel('Missing value %', fontsize=12, fontweight='bold')
ax1.set_ylabel('Number of analyses', fontsize=12, fontweight='bold')
ax1.set_title('Untarg missingness distribution\n(per analysis, n=1,885)',
              fontsize=12, fontweight='bold')
ax1.tick_params(labelsize=10)
for t in ax1.get_xticklabels() + ax1.get_yticklabels(): t.set_fontweight('bold')
ax1.legend(fontsize=10, prop={'weight': 'bold'}, framealpha=0.9)
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)
ax1.grid(axis='y', ls='--', alpha=0.3)

ax1.text(0.97, 0.97,
         f'Total: {n_total:,}\n'
         f'miss = 0%: {n_zero:,} ({n_zero/n_total*100:.1f}%)\n'
         f'miss > 0%: {n_any:,} ({n_any/n_total*100:.1f}%)\n'
         f'miss ≥ 50%: {n_50:,} ({n_50/n_total*100:.1f}%)\n'
         f'miss ≥ 80%: {n_80:,} ({n_80/n_total*100:.1f}%)\n'
         f'Mean: {mean:.1f}%  Median: {med:.1f}%',
         transform=ax1.transAxes, ha='right', va='top', fontsize=9, fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.4', fc='white', ec='#aaa', alpha=0.92))

# ── Right: scatter miss_pct vs n_feats ──────────────────────────────────────
ax2.scatter(df['n_feats'], df['miss_pct'],
            s=10, alpha=0.4, color='#5C6BC0', linewidths=0, zorder=3)
ax2.axhline(50, color='#FF9800', ls='--', lw=1.5, alpha=0.8, label='50% threshold')
ax2.set_xscale('log')
ax2.set_xlim(0.8, df['n_feats'].max() * 1.5)
ax2.set_ylim(-2, 102)
ax2.set_xlabel('Feature count in untarg (log scale)', fontsize=12, fontweight='bold')
ax2.set_ylabel('Missing value %', fontsize=12, fontweight='bold')
ax2.set_title('Does feature count predict untarg missingness?',
              fontsize=12, fontweight='bold')
ax2.tick_params(labelsize=10)
for t in ax2.get_xticklabels() + ax2.get_yticklabels(): t.set_fontweight('bold')
ax2.legend(fontsize=10, prop={'weight': 'bold'}, framealpha=0.9)
ax2.spines['top'].set_visible(False)
ax2.spines['right'].set_visible(False)
ax2.grid(ls='--', alpha=0.3)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot_untarg_missingness.{ext}', dpi=300, bbox_inches='tight')
print(f'Saved. n={n_total}, median={med:.1f}%, >=50%: {n_50}')
