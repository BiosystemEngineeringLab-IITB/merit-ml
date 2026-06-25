"""
Plot 3 — Adduct suffix comparison: datatable vs mwtab, per analysis.
Pattern: feature name ending in [+-]\\d+\\.\\d+ (MS m/z adduct mass).

For the same analysis ID, how many adduct-suffixed feature names appear
in datatable vs mwtab?

Left:  Sorted dumbbell chart — each row is one analysis with ≥1 adduct
       in either source; blue dot = datatable count, red dot = mwtab count.
Right: Per-analysis scatter dt_n_adduct vs mw_n_adduct (identity line).
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.lines import Line2D
from pathlib import Path

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype']  = 42

OUT = Path('/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-2/mwtab-datatable-difference')

df = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t')
n_total = len(df)

# Subset: analyses where at least one source has an adduct feature
d_any = df[(df['dt_n_adduct'] > 0) | (df['mw_n_adduct'] > 0)].copy()
d_any = d_any.sort_values('mw_n_adduct').reset_index(drop=True)

n_dt_only  = int(((d_any['dt_n_adduct'] > 0) & (d_any['mw_n_adduct'] == 0)).sum())
n_mw_only  = int(((d_any['mw_n_adduct'] > 0) & (d_any['dt_n_adduct'] == 0)).sum())
n_both     = int(((d_any['dt_n_adduct'] > 0) & (d_any['mw_n_adduct'] > 0)).sum())
n_neither  = int(((df['dt_n_adduct'] == 0) & (df['mw_n_adduct'] == 0)).sum())

fig = plt.figure(figsize=(14, 6))
gs  = gridspec.GridSpec(1, 2, wspace=0.32, width_ratios=[1.1, 1])
ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1])

# ── Left: dumbbell chart (horizontal) ─────────────────────────────────────────
y_pos = np.arange(len(d_any))

for i, row in d_any.iterrows():
    lo = min(row['dt_n_adduct'], row['mw_n_adduct'])
    hi = max(row['dt_n_adduct'], row['mw_n_adduct'])
    col = '#888' if lo == hi else ('#c62828' if row['mw_n_adduct'] > row['dt_n_adduct'] else '#1565C0')
    ax1.hlines(i, lo, hi, colors=col, linewidth=0.9, alpha=0.55, zorder=2)

ax1.scatter(d_any['dt_n_adduct'], y_pos,
            s=22, color='#1565C0', alpha=0.8, linewidths=0, zorder=3, label='datatable')
ax1.scatter(d_any['mw_n_adduct'], y_pos,
            s=22, color='#c62828', alpha=0.8, linewidths=0, zorder=4, label='mwtab')

ax1.set_xlabel('Number of adduct-suffixed features', fontsize=12, fontweight='bold')
ax1.set_ylabel(f'Analyses with ≥1 adduct (either source)  (n={len(d_any):,}, sorted by mwtab)',
               fontsize=10, fontweight='bold')
ax1.set_title('D3a — Adduct feature count: datatable (●) vs mwtab (●)\n'
              f'(per analysis, same analysis ID)',
              fontsize=12, fontweight='bold')
ax1.set_yticks([])
ax1.tick_params(labelsize=10)
for t in ax1.get_xticklabels(): t.set_fontweight('bold')
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)
ax1.spines['left'].set_visible(False)
ax1.grid(axis='x', ls='--', alpha=0.3)

legend_els = [
    Line2D([0], [0], marker='o', color='w', markerfacecolor='#1565C0', ms=8, label='datatable'),
    Line2D([0], [0], marker='o', color='w', markerfacecolor='#c62828', ms=8, label='mwtab'),
]
ax1.legend(handles=legend_els, fontsize=10, prop={'weight': 'bold'},
           loc='lower right', framealpha=0.9)

ax1.text(0.97, 0.97,
         f'Total analyses: {n_total:,}\n'
         f'No adducts (either): {n_neither:,} ({n_neither/n_total*100:.1f}%)\n'
         f'Adduct in datatable only: {n_dt_only}\n'
         f'Adduct in mwtab only: {n_mw_only}\n'
         f'Adduct in both: {n_both}',
         transform=ax1.transAxes, ha='right', va='top', fontsize=9, fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.4', fc='white', ec='#aaa', alpha=0.92))

# ── Right: per-analysis scatter dt_n_adduct vs mw_n_adduct ────────────────────
d_bg = df[(df['dt_n_adduct'] == 0) & (df['mw_n_adduct'] == 0)]
d_fg = df[(df['dt_n_adduct'] >  0) | (df['mw_n_adduct'] >  0)]

ax2.scatter(d_bg['dt_n_adduct'], d_bg['mw_n_adduct'],
            s=8, alpha=0.12, color='#aaa', linewidths=0, zorder=2,
            label=f'No adducts in either ({len(d_bg):,})')
ax2.scatter(d_fg['dt_n_adduct'], d_fg['mw_n_adduct'],
            s=55, alpha=0.85, color='#FF6F00', linewidths=0.5, edgecolors='black',
            zorder=4, label=f'≥1 adduct ({len(d_fg):,})')

lim = max(df['dt_n_adduct'].max(), df['mw_n_adduct'].max()) * 1.15 + 50
ax2.plot([0, lim], [0, lim], ls='--', lw=1.2, color='#555', alpha=0.5,
         label='Identity (y = x)', zorder=3)
ax2.set_xlim(-20, lim); ax2.set_ylim(-5, lim)
ax2.set_xlabel('# adduct features — datatable', fontsize=12, fontweight='bold')
ax2.set_ylabel('# adduct features — mwtab', fontsize=12, fontweight='bold')
ax2.set_title('D3b — Adduct count scatter: datatable vs mwtab\n(same analysis ID)',
              fontsize=12, fontweight='bold')
ax2.tick_params(labelsize=10)
for t in ax2.get_xticklabels() + ax2.get_yticklabels(): t.set_fontweight('bold')
ax2.legend(fontsize=9.5, prop={'weight': 'bold'}, loc='upper left', framealpha=0.9)
ax2.spines['top'].set_visible(False); ax2.spines['right'].set_visible(False)
ax2.grid(ls='--', alpha=0.3)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot3_adduct_inflation.{ext}', dpi=300, bbox_inches='tight')
print(f'Plot 3 saved.  analyses with any adduct={len(d_any)}, dt-only={n_dt_only}, mw-only={n_mw_only}, both={n_both}')
