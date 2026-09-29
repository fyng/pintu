"""Gene importance scores, ranked, as vertical bars."""

import numpy as np

from ._style import COLORS, panel_figure

GENES = ["TP53", "KRAS", "EGFR", "PIK3CA", "BRAF", "PTEN", "APC", "CDKN2A", "SMAD4", "ARID1A",
         "NOTCH1", "FBXW7"]


def data():
    rng = np.random.default_rng(4)
    s = np.sort(rng.gamma(2.0, 1.0, len(GENES)))[::-1]
    return GENES, s


def scores(w, h):
    fig = panel_figure(w, h)
    ax = fig.add_subplot()
    genes, s = data()
    ax.bar(genes, s, color=COLORS[0], width=0.7)
    ax.set_ylabel("Importance score")
    ax.set_xlabel("Gene")
    ax.tick_params(axis="x", rotation=45)
    return fig
