"""Faster, bit-identical Spearman blocks for Redundant features (8791 only; 8782's merit/correlation_v9.py is unchanged).

merit/correlation_v9.py computes matrices with missing cells with six float64 matrix products per block. Ranks are whole or half
numbers (average ranks), so every value, product and partial sum of those products is a multiple of 1/4. float32 holds every
multiple of 1/4 up to 2**22 exactly, so when the largest possible sum (the sum of the squared ranks of a feature) stays below 2**22
the float32 products are exact and equal to the float64 ones, whatever the summation order of the BLAS. Then the products are
converted to float64 and the rest is the original code: same quotient, clipping, validity, diagonal, sums, stored pairs and order.
The output is therefore identical, bit for bit, to correlation_v9.spearman_blocks; only the speed of the products changes (float32
products are about twice as fast and need half the memory). When the bound does not hold (very many samples) the original float64
code runs. Matrices without missing cells use the original code (already float32).
Switch: MERIT_FAST_CORRELATION=0 restores the original code everywhere.
"""
from __future__ import annotations

import os

import numpy as np

ENABLED = os.environ.get('MERIT_FAST_CORRELATION', '1') != '0'
EXACT_LIMIT = 2.0 ** 22          # multiples of 1/4 below this are exact in float32 (24-bit significand)
STATS = {'exact_float32_matrices': 0, 'float64_matrices': 0, 'complete_matrices': 0}


def exact_in_float32(prep: dict) -> bool:
    """True when every product of the six matrix products is exactly representable in float32."""
    x0, x2 = prep['x0'], prep['x2']
    if x0.size == 0:
        return True
    q = 4 * x0
    if not np.array_equal(q, np.round(q)):                     # not multiples of 1/4 (never for average ranks; kept as a guard)
        return False
    bound = max(float(x2.sum(axis=0).max(initial=0)), float(np.abs(x0).sum(axis=0).max(initial=0)) ** 2 / max(x0.shape[0], 1),
                float(prep['m'].sum(axis=0).max(initial=0)))
    # every partial sum of x0_i*x0_j, x2_i*m_j, x0_i*m_j, m_i*m_j is at most max(sum x2) (Cauchy-Schwarz) or the larger bounds above
    return float(x2.sum(axis=0).max(initial=0)) < EXACT_LIMIT and bound < EXACT_LIMIT


def install(cv) -> bool:
    """Wrap correlation_v9.prepare and compute_block; spearman_blocks and the block phase of the stage then use them unchanged."""
    if not ENABLED or getattr(cv.compute_block, '_fast', False):
        return ENABLED
    original_prepare, original_block = cv.prepare, cv.compute_block

    def prepare(sub):
        p = original_prepare(sub)
        if p['complete']:
            STATS['complete_matrices'] += 1
        elif exact_in_float32(p):
            STATS['exact_float32_matrices'] += 1
            p = {**p, 'x0': np.ascontiguousarray(p['x0'], dtype=np.float32), 'm': np.ascontiguousarray(p['m'], dtype=np.float32),
                 'x2': np.ascontiguousarray(p['x2'], dtype=np.float32), 'exact_float32': True}
        else:
            STATS['float64_matrices'] += 1
        return p

    def compute_block(prep, a, b, store_min=cv.STORE_MIN):
        if prep['complete'] or not prep.get('exact_float32') and prep['x0'].dtype != np.float32:
            return original_block(prep, a, b, store_min)
        x0, m, x2 = prep['x0'], prep['m'], prep['x2']
        f64 = lambda v: v.astype(np.float64)                       # exact products, now in the original float64 code
        nn = f64(m[:, a:b].T @ m)
        sx = f64(x0[:, a:b].T @ m); sy = f64(m[:, a:b].T @ x0)
        sxx = f64(x2[:, a:b].T @ m); syy = f64(m[:, a:b].T @ x2); sxy = f64(x0[:, a:b].T @ x0)
        # the original formula, num = nn*sxy - sx*sy, den = sqrt((nn*sxx - sx**2) * (nn*syy - sy**2)), c = num/den, with the same
        # operations in the same order (x**2 is x*x in NumPy), written into existing arrays instead of new temporaries
        tmp = np.empty_like(nn)
        with np.errstate(all="ignore"):
            np.multiply(nn, sxx, out=sxx); np.multiply(sx, sx, out=tmp); np.subtract(sxx, tmp, out=sxx)
            np.multiply(nn, syy, out=syy); np.multiply(sy, sy, out=tmp); np.subtract(syy, tmp, out=syy)
            np.multiply(sxx, syy, out=sxx); den = np.sqrt(sxx, out=sxx)
            np.multiply(nn, sxy, out=sxy); np.multiply(sx, sy, out=tmp); np.subtract(sxy, tmp, out=sxy)
            c = np.divide(sxy, den, out=sxy)
        valid = (nn >= cv.MIN_PAIR) & np.isfinite(c) & (den > 0)
        del nn, sx, sy, sxx, syy, tmp, den
        c[~valid] = 0.0; np.clip(c, -1.0, 1.0, out=c)          # = np.clip(np.where(valid, c, 0.0), -1.0, 1.0)
        rows = np.arange(a, b)
        c[rows - a, rows] = 1.0; valid[rows - a, rows] = True
        ac = np.abs(c)
        ii, jj = np.nonzero((ac >= store_min) & valid)
        keep = jj > ii + a
        ii, jj = ii[keep], jj[keep]
        return ac.sum(axis=1), valid.sum(axis=1), (ii + a).astype(np.int32), jj.astype(np.int32), c[ii, jj].astype(np.float32)

    prepare._fast = compute_block._fast = True
    cv.prepare, cv.compute_block = prepare, compute_block
    return True
