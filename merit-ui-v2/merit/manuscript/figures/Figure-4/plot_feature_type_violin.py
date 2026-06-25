"""
Grouped violin plot — named metabolite vs mz/RT feature counts per analysis
across three MW sources. Y-axis log scale. Pub-ready style.
"""
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
from pathlib import Path
from scipy.stats import gaussian_kde
from statistics import median

plt.rcParams['font.family']   = 'DejaVu Sans'
plt.rcParams['pdf.fonttype']  = 42
plt.rcParams['ps.fonttype']   = 42

OUT  = Path('/home/shayantan/metabolomics/ML-ready/merit/manuscript/figures/Figure-4')
DATA = Path('/tmp/feature_type_counts.json')

with open(DATA) as f:
    raw = json.load(f)

# ── palette ───────────────────────────────────────────────────────────────────
C_NAMED = {'dt': '#4472C4', 'mw': '#70AD47', 'ut': '#E67E22'}   # per-source named
C_MZRT  = {'dt': '#AAAAAA', 'mw': '#AAAAAA', 'ut': '#AAAAAA'}   # uniform grey for mz/RT

LABELS  = {'dt': 'Datatable', 'mw': 'mwTab', 'ut': 'Untarg data'}

# ── group positions: 3 sources × 2 types, small gap within, large gap between
GAP_INNER = 0.36   # named vs mzrt within same source
GAP_OUTER = 1.0    # between sources
positions = {}
center = 0
for src in ['dt', 'mw', 'ut']:
    positions[(src, 'named')] = center - GAP_INNER / 2
    positions[(src, 'mzrt')]  = center + GAP_INNER / 2
    center += GAP_OUTER

# ── helper: log-space KDE violin ─────────────────────────────────────────────
MAX_N = 4990   # largest group — sets the reference width

def draw_violin(ax, data, pos, color, width=0.13):
    data = [x for x in data if x > 0]
    if len(data) < 5:
        return
    # scale width by sqrt(n) relative to largest group
    scaled_width = width * np.sqrt(len(data) / MAX_N)
    scaled_width = max(scaled_width, 0.025)   # minimum visible width
    log_data = np.log10(data)
    kde = gaussian_kde(log_data, bw_method=0.30)
    y_log = np.linspace(log_data.min(), log_data.max(), 400)
    density = kde(y_log)
    density = density / density.max() * scaled_width
    y_orig = 10 ** y_log
    ax.fill_betweenx(y_orig, pos - density, pos + density,
                     color=color, alpha=0.75, linewidth=0, zorder=3)
    ax.plot(pos - density, y_orig, color=color, lw=0.5, alpha=0.6)
    ax.plot(pos + density, y_orig, color=color, lw=0.5, alpha=0.6)


def draw_box(ax, data, pos, color):
    data = [x for x in data if x > 0]
    if len(data) < 5:
        return
    q1, med, q3 = np.percentile(data, [25, 50, 75])
    iqr = q3 - q1
    lo  = max(min(data), q1 - 1.5 * iqr)
    hi  = min(max(data), q3 + 1.5 * iqr)
    bw  = 0.06
    rect = plt.Rectangle((pos - bw/2, q1), bw, q3 - q1,
                          facecolor='white', edgecolor='#333', lw=1.2, zorder=5)
    ax.add_patch(rect)
    ax.plot([pos - bw/2, pos + bw/2], [med, med],
            color='#111', lw=2.0, zorder=6)
    ax.plot([pos, pos], [lo, q1], color='#444', lw=1.0, zorder=4)
    ax.plot([pos, pos], [q3, hi], color='#444', lw=1.0, zorder=4)
    for y in (lo, hi):
        ax.plot([pos - 0.04, pos + 0.04], [y, y], color='#444', lw=1.0, zorder=4)
    return int(med)


# ── figure ────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(9, 6))

for src in ['dt', 'mw', 'ut']:
    records = raw[src]
    named_counts = [r['named'] for r in records]
    mzrt_counts  = [r['mzrt']  for r in records]

    p_named = positions[(src, 'named')]
    p_mzrt  = positions[(src, 'mzrt')]

    draw_violin(ax, named_counts, p_named, C_NAMED[src])
    med_named = draw_box(ax, named_counts, p_named, C_NAMED[src])

    draw_violin(ax, mzrt_counts,  p_mzrt,  C_MZRT[src])
    med_mzrt  = draw_box(ax, mzrt_counts,  p_mzrt,  C_MZRT[src])

    # median annotations
    named_nonzero = [x for x in named_counts if x > 0]
    mzrt_nonzero  = [x for x in mzrt_counts  if x > 0]
    n_named_zero  = sum(1 for x in named_counts if x == 0)
    n_mzrt_zero   = sum(1 for x in mzrt_counts  if x == 0)

    # Row 1 (top): n= counts
    ax.text(p_named - 0.03, 0.975, f'n={len(named_nonzero):,}',
            ha='right', va='top', fontsize=8, fontweight='bold', color='#444',
            transform=ax.get_xaxis_transform())

    if len(mzrt_nonzero) > 0:
        ax.text(p_mzrt + 0.03, 0.975, f'n={len(mzrt_nonzero):,}',
                ha='left', va='top', fontsize=8, fontweight='bold', color='#777',
                transform=ax.get_xaxis_transform())
    else:
        ax.text(p_mzrt + 0.03, 0.975, 'n=0',
                ha='left', va='top', fontsize=8, color='#aaa',
                transform=ax.get_xaxis_transform())

    # Row 2: median values
    if named_nonzero:
        ax.text(p_named - 0.03, 0.925, f'med={int(median(named_nonzero)):,}',
                ha='right', va='top', fontsize=8.5, fontweight='bold',
                color=C_NAMED[src], transform=ax.get_xaxis_transform())

    if mzrt_nonzero:
        ax.text(p_mzrt + 0.03, 0.925, f'med={int(median(mzrt_nonzero)):,}',
                ha='left', va='top', fontsize=8.5, fontweight='bold',
                color='#666', transform=ax.get_xaxis_transform())

# ── x-axis: source labels centred between pairs ───────────────────────────────
src_centers = [(positions[(s,'named')] + positions[(s,'mzrt')]) / 2 for s in ['dt','mw','ut']]
ax.set_xticks(src_centers)
ax.set_xticklabels([LABELS[s] for s in ['dt', 'mw', 'ut']],
                   fontsize=12, fontweight='bold')
ax.set_xlim(positions[('dt','named')] - 0.28, positions[('ut','mzrt')] + 0.28)

# ── y-axis ────────────────────────────────────────────────────────────────────
ax.set_yscale('log')
ax.set_ylim(0.8, 1.5e5)
ax.yaxis.set_major_formatter(
    mticker.FuncFormatter(lambda x, _: f'{int(x):,}' if x >= 1 else f'{x:.1f}'))
ax.set_ylabel('Feature count per analysis  (log scale)',
              fontsize=12, fontweight='bold')
ax.tick_params(axis='y', labelsize=11)
for t in ax.get_yticklabels():
    t.set_fontweight('bold')

# ── legend ────────────────────────────────────────────────────────────────────
legend_handles = [
    mpatches.Patch(color='#4472C4', alpha=0.75, label='Named metabolites — Datatable'),
    mpatches.Patch(color='#70AD47', alpha=0.75, label='Named metabolites — mwTab'),
    mpatches.Patch(color='#E67E22', alpha=0.75, label='Named metabolites — Untarg data'),
    mpatches.Patch(color='#BBBBBB', alpha=0.75, label='Unannotated features (mz/RT, NMR bins, IDs)'),
]
ax.legend(handles=legend_handles, fontsize=10, prop={'weight': 'bold'},
          loc='lower right', framealpha=0.92)

# ── title and grid ────────────────────────────────────────────────────────────
ax.set_title('Feature identity composition per analysis across MW sources\n'
             '(named metabolites vs unannotated features, log scale)',
             fontsize=14, fontweight='bold')
ax.yaxis.grid(True, which='major', linestyle='--', linewidth=0.6,
              color='#cccccc', zorder=0)
ax.set_axisbelow(True)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)

fig.tight_layout()
for ext in ('pdf', 'png', 'svg'):
    fig.savefig(OUT / f'plot_feature_type_violin.{ext}', dpi=300, bbox_inches='tight')
    print(f'Saved: {OUT}/plot_feature_type_violin.{ext}')
plt.close(fig)
