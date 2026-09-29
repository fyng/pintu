"""Patient timeline on a synthetic cohort, loaded slowly and kept with `cache`."""

import time

import matplotlib
import numpy as np

from .common import RC, axes_mm, figure

try:
    from pintu_sdk import cache
except ImportError:  # recipes also run without the SDK
    def cache(fn, *args, **kwargs):
        return fn(*args, **kwargs)

LINES = ["Chemo", "Targeted", "Immuno"]


def load_cohort(seed):
    """Stands in for a slow data load (a large file or a database query)."""
    time.sleep(1.5)
    rng = np.random.default_rng(seed)
    cohort = {}
    for p in range(1, 21):
        starts = np.sort(rng.uniform(0, 30, 3))
        lines = [(LINES[i], s, s + rng.uniform(2, 8)) for i, s in enumerate(starts)]
        days = np.sort(rng.uniform(0, 40, 25))
        cohort[f"P{p:02d}"] = {"lines": lines, "labs": (days, 50 + np.cumsum(rng.normal(0, 4, 25))),
                              "events": sorted(rng.uniform(0, 40, 4))}
    return cohort


def timeline(w, h, patient="P01", seed=3):
    """Treatment lines and events; from 120 mm wide, adds a lab value track."""
    pt = cache(load_cohort, seed)[patient]
    with matplotlib.rc_context(RC):
        fig = figure(w, h)
        labs = w >= 120
        track_h = (h - 17) / 2 if labs else h - 15
        ax = axes_mm(fig, w, h, left=14, top=5, right=2, bottom=h - 5 - track_h)
        for i, (name, s, e) in enumerate(pt["lines"]):
            ax.barh(i, e - s, left=s, height=0.6, color=f"C{i}")
        ax.set_yticks(range(len(pt["lines"])), [n for n, _, _ in pt["lines"]])
        ax.vlines(pt["events"], -0.5, len(pt["lines"]) - 0.5, color="0.2", lw=0.6, ls=":")
        ax.set(xlim=(0, 40), ylim=(-0.5, len(pt["lines"]) - 0.5), title=patient)
        if labs:
            lx = axes_mm(fig, w, h, left=14, top=h - 8 - track_h, right=2, bottom=9.5)
            lx.plot(*pt["labs"], color="0.3", marker="o", ms=1.5)
            lx.set(xlim=(0, 40), ylabel="CA-125")
            ax.set_xticklabels([])
        (lx if labs else ax).set_xlabel("Months")
        return fig
