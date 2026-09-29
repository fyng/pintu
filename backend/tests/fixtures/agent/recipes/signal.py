"""A biomarker trace with confidence band, events and annotations."""

import numpy as np

from ._style import COLORS, panel_figure


def data():
    rng = np.random.default_rng(3)
    t = np.linspace(0, 365, 120)
    y = 20 + 10 * np.sin(t / 60) + rng.normal(0, 1.5, t.size)
    events = [45, 150, 290]
    return t, y, events


def trace(w, h):
    fig = panel_figure(w, h)
    ax = fig.add_subplot()
    t, y, events = data()
    ax.plot(t, y, color=COLORS[0], label="ctDNA")
    ax.fill_between(t, y - 3, y + 3, color=COLORS[0], alpha=0.2, lw=0, label="95% CI")
    for e in events:
        ax.axvline(e, color="0.5", lw=0.5, ls="--")
    ax.annotate("Progression", xy=(290, 30), xytext=(220, 36), arrowprops={"arrowstyle": "->", "lw": 0.5})
    ax.plot(t[::6], np.full(t[::6].size, 6), "|", color="0.3", ms=4, label="Sample drawn")
    ax.set_title("ctDNA over the first year of treatment")
    ax.set_xlabel("Days since start")
    ax.set_ylabel("ctDNA (copies/mL)")
    ax.grid(axis="y", lw=0.3, color="0.85")
    ax.legend(frameon=False, loc="upper left", ncol=3)
    ax.set_ylim(0, 42)
    return fig
