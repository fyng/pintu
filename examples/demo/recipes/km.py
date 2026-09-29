"""Kaplan-Meier curves on synthetic survival data."""

import matplotlib
import numpy as np

from .common import RC, axes_mm, figure

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]


def _data(groups, n, seed):
    rng = np.random.default_rng(seed)
    out = []
    for g in range(groups):
        t = rng.exponential(24 * (1 + 0.6 * g), n)
        c = rng.uniform(6, 60, n)
        out.append((np.minimum(t, c), t <= c))
    return out


def _km(time, event):
    order = np.argsort(time)
    time, event = time[order], event[order]
    at_risk = len(time) - np.arange(len(time))
    surv = np.cumprod(1 - event / at_risk)
    return np.r_[0, time], np.r_[1, surv]


def km(w, h, groups=2, n=80, seed=1):
    """KM curves per group; from 110 mm wide, adds a number-at-risk column."""
    with matplotlib.rc_context(RC):
        fig = figure(w, h)
        side = 32 if w >= 110 else 0
        ax = axes_mm(fig, w, h, left=10.5, top=5, right=2 + side, bottom=9.5)
        data = _data(groups, n, seed)
        for g, (t, e) in enumerate(data):
            x, y = _km(t, e)
            ax.step(x, y, where="post", color=COLORS[g % 4], label=f"group {g + 1}")
        ax.set(xlabel="Months", ylabel="Survival", ylim=(0, 1.02), xlim=(0, 60))
        ax.legend(frameon=False, loc="lower left")
        if side:
            tab = axes_mm(fig, w, h, left=w - side + 7, top=5, right=2, bottom=9.5)
            ticks = [0, 12, 24, 36, 48]
            for g, (t, _) in enumerate(data):
                tab.plot(ticks, [(t >= k).sum() for k in ticks], "o-", ms=2, color=COLORS[g % 4])
            tab.set(xlabel="Months", ylabel="At risk", xticks=ticks)
        return fig
