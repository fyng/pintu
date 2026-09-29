"""Four treatment groups over time, on one axes."""

import numpy as np

from ._style import COLORS, panel_figure

GROUPS = ["Control", "Low dose", "Mid dose", "High dose"]


def data():
    rng = np.random.default_rng(1)
    t = np.arange(0, 25)
    out = {}
    for k, g in enumerate(GROUPS):
        mean = 100 * np.exp(-0.02 * k * t) + rng.normal(0, 3, t.size)
        out[g] = (t, mean, 4 + 0.2 * t)
    return out


def trends(w, h):
    fig = panel_figure(w, h)
    ax = fig.add_subplot()
    for k, (g, (t, m, s)) in enumerate(data().items()):
        ax.plot(t, m, color=COLORS[k], label=g)
        ax.fill_between(t, m - s, m + s, color=COLORS[k], alpha=0.15, lw=0)
    ax.set_xlabel("Day")
    ax.set_ylabel("Tumour volume (%)")
    ax.legend(frameon=False, ncol=2)
    return fig
