"""Cell-type composition per sample, stacked bars with a side legend."""

import numpy as np

from ._style import COLORS, panel_figure

TYPES = ["T cell", "B cell", "Myeloid", "Stroma", "Tumour"]
SAMPLES = [f"S{i}" for i in range(1, 9)]


def data():
    rng = np.random.default_rng(2)
    x = rng.dirichlet(np.ones(len(TYPES)) * 2, size=len(SAMPLES))
    return x


def stacked(w, h):
    fig = panel_figure(w, h)
    ax = fig.add_subplot()
    x = data()
    bottom = np.zeros(len(SAMPLES))
    for k, t in enumerate(TYPES):
        ax.bar(SAMPLES, x[:, k], bottom=bottom, color=COLORS[k], label=t, width=0.8)
        bottom += x[:, k]
    ax.set_ylabel("Fraction of cells")
    ax.set_xlabel("Sample")
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.0, 0.5), title="Cell type")
    return fig
