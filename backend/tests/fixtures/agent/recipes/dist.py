"""Response distributions per arm, as box plots."""

import numpy as np

from ._style import COLORS, panel_figure

ARMS = ["Placebo", "Arm A", "Arm B", "Arm C"]


def data(n):
    rng = np.random.default_rng(5)
    return [rng.normal(10 + 3 * k, 2 + k * 0.5, n) for k in range(len(ARMS))]


def boxes(w, h, n=40):
    fig = panel_figure(w, h)
    ax = fig.add_subplot()
    ax.boxplot(data(n), tick_labels=ARMS, widths=0.5, patch_artist=True,
               boxprops={"facecolor": COLORS[0] + "40", "lw": 0.5},
               medianprops={"color": COLORS[3], "lw": 1.0},
               whiskerprops={"lw": 0.5}, capprops={"lw": 0.5}, flierprops={"ms": 2})
    ax.set_ylabel("Response (a.u.)")
    return fig
