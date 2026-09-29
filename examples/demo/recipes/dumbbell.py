"""Dumbbell plot of paired values on synthetic data."""

import matplotlib
import numpy as np

from .common import RC, axes_mm, figure


def dumbbell(w, h, n=10, seed=2):
    """Before/after per item; from 100 mm wide, adds a column of differences."""
    rng = np.random.default_rng(seed)
    a = rng.uniform(10, 60, n)
    b = a + rng.normal(8, 10, n)
    order = np.argsort(b - a)
    a, b = a[order], b[order]
    names = [f"item {i + 1}" for i in order]
    y = np.arange(n)
    with matplotlib.rc_context(RC):
        fig = figure(w, h)
        side = 0.3 * w if w >= 100 else 0
        ax = axes_mm(fig, w, h, left=12, top=5, right=3 + side, bottom=9.5)
        ax.hlines(y, a, b, color="0.7", lw=1.2)
        ax.scatter(a, y, s=8, color="#1f77b4", zorder=3, label="before")
        ax.scatter(b, y, s=8, color="#d62728", zorder=3, label="after")
        ax.set_yticks(y, names)
        ax.set_xlabel("Value")
        ax.legend(frameon=False, loc="lower right")
        if side:
            d = axes_mm(fig, w, h, left=w - side + 2, top=5, right=2, bottom=9.5)
            d.barh(y, b - a, color=np.where(b > a, "#d62728", "#1f77b4"), height=0.6)
            d.axvline(0, color="0.3", lw=0.5)
            d.set_yticks(y, [])
            d.set_xlabel("Change")
        return fig
