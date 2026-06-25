"""
Plot 5 — mwtab block type and its effect on the datatable↔mwtab feature count gap.
Block type is an mwtab-only property, but anchored to the same analysis ID.

For the same analysis ID, the mwtab block type determines the data modality
(MS vs NMR). We ask: does platform type explain how much larger mwtab feature
counts are compared to datatable?

Left:  Violin + box of feat_diff (n_mw - n_dt) grouped by platform type
       (NMR_METABOLITE_DATA, EXTENDED_NMR, MS_METABOLITE_DATA, EXTENDED_MS).
Right: Scatter n_dt_feats vs n_mw_feats, coloured by NMR vs MS, with
       identity line — per analysis, same analysis ID.
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

df = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t')
n_total = len(df)

# Clean labels
LABEL_MAP = {
    'MS_METABOLITE_DATA_START':              'MS',
    'NMR_METABOLITE_DATA_START':             'NMR',
    'EXTENDED_NMR_METABOLITE_DATA_START':    'EXT-NMR',
    'EXTENDED_MS_METABOLITE_DATA_START':     'EXT-MS',
}
df['platform'] = df['block_type'].map(LABEL_MAP).fillna('Other')

ORDER  = ['MS', 'EXT-MS', 'NMR', 'EXT-NMR']
COLORS = {'MS': '#1565C0', 'EXT-MS': '#42A5F5', 'NMR': '#c62828', 'EXT-NMR': '#EF9A9A'}

fig = plt.figure(figsize=(14, 6))
gs  = gridspec.GridSpec(1, 2, wspace=0.32, width_ratios=[1.1, 1])
ax1 = fig.add_subplot(gs[0])
ax2 = fig.add_subplot(gs[1])

# ── Left: violin + box of feat_diff by platform ────────────────────────────────
groups   = [df.loc[df['platform'] == p, 'feat_diff'].values for p in ORDER]
labels   = [f'{p}\n(n={len(g):,})' for p, g in zip(ORDER, groups)]
colors_v = [COLORS[p] for p in ORDER]
positions = list(range(1, len(ORDER) + 1))

# filter to non-empty groups
valid = [(pos, grp, lbl, col) for pos, grp, lbl, col in
         zip(positions, groups, labels, colors_v) if len(grp) > 1]
v_pos  = [x[0] for x in valid]
v_data = [x[1] for x in valid]
v_lbl  = [x[2] for x in valid]
v_col  = [x[3] for x in valid]

vp = ax1.violinplot(v_data, positions=v_pos, widths=0.55,
                    showmedians=False, showextrema=False)
for pc, col in zip(vp['bodies'], v_col):
    pc.set_facecolor(col); pc.set_alpha(0.35)
    pc.set_edgecolor('black'); pc.set_linewidth(0.8)

bp = ax1.boxplot(v_data, positions=v_pos, widths=0.18,
                 patch_artist=True, notch=False,
                 medianprops=dict(color='black', linewidth=2.2),
                 boxprops=dict(facecolor='white', linewidth=1.0),
                 whiskerprops=dict(linewidth=1.0, linestyle='--'),
                 capprops=dict(linewidth=1.0),
                 flierprops=dict(marker='o', ms=3, alpha=0.25, linewidth=0))

for pos, arr, col in zip(v_pos, v_data, v_col):
    med = float(np.median(arr))
    ax1.text(pos, med + max(arr) * 0.02 + 0.5,
             f'{med:+.0f}', ha='center', va='bottom',
             fontsize=10, fontweight='bold', color=col)

ax1.set_yscale('symlog', linthresh=1)
ax1.axhline(0, color='#555', ls='--', lw=1.4, alpha=0.55)
ax1.set_xticks(v_pos)
ax1.set_xticklabels(v_lbl, fontsize=11, fontweight='bold')
ax1.set_ylabel('Feature count difference  (mwtab − datatable)\nper analysis  [symlog scale]', fontsize=11, fontweight='bold')
ax1.set_title('D5a — Does mwtab block type predict feature count gap?\n'
              '(same analysis ID: n_mwtab − n_datatable)',
              fontsize=12, fontweight='bold')
ax1.tick_params(labelsize=10)
for t in ax1.get_yticklabels(): t.set_fontweight('bold')
ax1.spines['top'].set_visible(False); ax1.spines['right'].set_visible(False)
ax1.grid(axis='y', ls='--', alpha=0.3)

n_nmr = int(df['is_nmr'].sum())
n_ms  = n_total - n_nmr
ax1.text(0.97, 0.97,
         f'MS analyses:  {n_ms:,} ({n_ms/n_total*100:.1f}%)\n'
         f'NMR analyses: {n_nmr:,} ({n_nmr/n_total*100:.1f}%)\n'
         f'Median diff (MS):  {df[df["is_nmr"]==0]["feat_diff"].median():+.0f}\n'
         f'Median diff (NMR): {df[df["is_nmr"]==1]["feat_diff"].median():+.0f}',
         transform=ax1.transAxes, ha='right', va='top', fontsize=9, fontweight='bold',
         bbox=dict(boxstyle='round,pad=0.4', fc='white', ec='#aaa', alpha=0.92))

# ── Right: scatter dt_feats vs mw_feats coloured by NMR/MS ────────────────────
d = df[(df['n_dt_feats'] > 0) & (df['n_mw_feats'] > 0)]
ms_d  = d[d['is_nmr'] == 0]
nmr_d = d[d['is_nmr'] == 1]

ax2.scatter(ms_d['n_dt_feats'],  ms_d['n_mw_feats'],
            s=12, alpha=0.35, color='#1565C0', linewidths=0,
            label=f'MS  (n={len(ms_d):,})', zorder=3)
ax2.scatter(nmr_d['n_dt_feats'], nmr_d['n_mw_feats'],
            s=30, alpha=0.75, color='#c62828', marker='^', linewidths=0.5,
            edgecolors='white', label=f'NMR (n={len(nmr_d):,})', zorder=4)

lim = max(d['n_dt_feats'].max(), d['n_mw_feats'].max()) * 1.5
ax2.plot([1, lim], [1, lim], ls='--', lw=1.4, color='#555', alpha=0.6,
         label='Identity (y = x)', zorder=2)
ax2.set_xscale('log'); ax2.set_yscale('log')
ax2.set_xlim(0.8, lim); ax2.set_ylim(0.8, lim)
ax2.set_xlabel('Feature count — datatable', fontsize=12, fontweight='bold')
ax2.set_ylabel('Feature count — mwtab', fontsize=12, fontweight='bold')
ax2.set_title('D5b — Feature count scatter by platform\n'
              '(same analysis ID, log-log)',
              fontsize=12, fontweight='bold')
ax2.tick_params(labelsize=10)
for t in ax2.get_xticklabels() + ax2.get_yticklabels(): t.set_fontweight('bold')
ax2.legend(fontsize=10, prop={'weight': 'bold'}, loc='upper left', framealpha=0.9)
ax2.spines['top'].set_visible(False); ax2.spines['right'].set_visible(False)
ax2.grid(ls='--', alpha=0.3)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot5_block_types.{ext}', dpi=300, bbox_inches='tight')
print(f'Plot 5 saved.  MS={n_ms} ({n_ms/n_total*100:.1f}%), NMR={n_nmr} ({n_nmr/n_total*100:.1f}%)')
