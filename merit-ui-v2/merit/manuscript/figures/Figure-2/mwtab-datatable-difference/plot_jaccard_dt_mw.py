"""
Plot 2 — Jaccard(datatable, mwtab) distribution for 4,464 combo-110 analyses.
Split-axis histogram: zero bar at left, log-scale non-zero bars.
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['pdf.fonttype'] = 42
plt.rcParams['ps.fonttype']  = 42

OUT = Path('/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-2/mwtab-datatable-difference')

df = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t')
j = df['jaccard_dt_mw'].values

n_total   = len(j)
n_zero    = int((j == 0).sum())
n_nonzero = int((j >  0).sum())
nz        = j[j > 0]
pct_zero  = n_zero / n_total * 100

# bins for non-zero part (log scale)
LOG_BINS    = np.linspace(-3, 0, 31)
BIN_EDGES   = 10 ** LOG_BINS
counts_nz, _ = np.histogram(nz, bins=BIN_EDGES)
bin_centers_log = (LOG_BINS[:-1] + LOG_BINS[1:]) / 2

n_low  = int((nz <  0.5).sum())   # low overlap
n_high = int((nz >= 0.5).sum())   # high overlap (≥ 0.5)
pct_low  = n_low  / n_total * 100
pct_high = n_high / n_total * 100

ZERO_POS = -3.6

fig, ax = plt.subplots(figsize=(9, 5.5))

# Zero bar
ax.bar(ZERO_POS, n_zero, width=0.55, color='#1565C0', alpha=0.85,
       edgecolor='white', linewidth=0.8, zorder=3)
ax.text(ZERO_POS, n_zero,
        f'  {n_zero} ({pct_zero:.1f}%)',
        ha='left', va='bottom', fontsize=10, fontweight='bold', color='#1565C0')

# Non-zero bars
THRESH_LOG = np.log10(0.5)
for cnt, clog in zip(counts_nz, bin_centers_log):
    if cnt == 0:
        continue
    color = '#43A047' if clog >= THRESH_LOG else '#FF9800'
    ax.bar(clog, cnt, width=(LOG_BINS[1] - LOG_BINS[0]) * 0.88,
           color=color, alpha=0.80, edgecolor='white', linewidth=0.5, zorder=3)

# Divider between zero and log axis
ax.axvline(-3.3, color='#555', ls='--', lw=1.5, alpha=0.55)
# threshold line
ax.axvline(THRESH_LOG, color='#2E7D32', ls=':', lw=1.5, alpha=0.7)

ymax = max(n_zero, counts_nz.max()) * 1.25

peak_low  = int(counts_nz[bin_centers_log <  THRESH_LOG].max()) if any(bin_centers_log <  THRESH_LOG) else 1
peak_high = int(counts_nz[bin_centers_log >= THRESH_LOG].max()) if any(bin_centers_log >= THRESH_LOG) else 1

ax.annotate(
    f'J < 0.5: {n_low} analyses\n(partial feature overlap)',
    xy=(-2.2, peak_low + ymax * 0.02),
    xytext=(-3.0, ymax * 0.60),
    fontsize=9, fontweight='bold', color='#E65100',
    arrowprops=dict(arrowstyle='->', color='#E65100', lw=1.0),
    bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#FFB74D', alpha=0.9)
)
ax.annotate(
    f'J ≥ 0.5: {n_high} analyses\n(high feature concordance)',
    xy=(-0.1, peak_high + ymax * 0.02),
    xytext=(-1.5, ymax * 0.75),
    fontsize=9, fontweight='bold', color='#2E7D32',
    arrowprops=dict(arrowstyle='->', color='#2E7D32', lw=1.0),
    bbox=dict(boxstyle='round,pad=0.3', fc='white', ec='#81C784', alpha=0.9)
)

ax.set_xticks([ZERO_POS, -3, -2, -1, -0.3, 0])
ax.set_xticklabels(['J = 0', '0.001', '0.01', '0.1', '0.5', '1.0'],
                   fontsize=11, fontweight='bold')
ax.set_xlim(-3.95, 0.22)
ax.set_ylim(0, ymax)
ax.set_xlabel('Jaccard similarity  |datatable ∩ mwtab| / |datatable ∪ mwtab|',
              fontsize=12, fontweight='bold')
ax.set_ylabel('Number of analyses', fontsize=12, fontweight='bold')
ax.set_title('D2 — Feature name concordance: datatable ↔ mwtab\n'
             f'({n_total:,} combo-110 analyses)',
             fontsize=14, fontweight='bold')
ax.tick_params(labelsize=11)
for t in ax.get_yticklabels():
    t.set_fontweight('bold')

legend_handles = [
    mpatches.Patch(color='#1565C0', alpha=0.85,
                   label=f'Zero overlap — {n_zero} ({pct_zero:.1f}%)'),
    mpatches.Patch(color='#FF9800', alpha=0.8,
                   label=f'Partial concordance (J < 0.5) — {n_low} ({pct_low:.1f}%)'),
    mpatches.Patch(color='#43A047', alpha=0.8,
                   label=f'High concordance (J ≥ 0.5) — {n_high} ({pct_high:.1f}%)'),
]
ax.legend(handles=legend_handles, fontsize=10, prop={'weight': 'bold'},
          loc='upper left', framealpha=0.9)

ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
ax.grid(axis='y', linestyle='--', alpha=0.35)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot2_jaccard_dt_mw.{ext}', dpi=300, bbox_inches='tight')
print(f'Plot 2 saved.  zero={n_zero} ({pct_zero:.1f}%), low={n_low}, high={n_high}')
