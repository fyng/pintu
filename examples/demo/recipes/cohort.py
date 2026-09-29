"""Timelines for a synthetic 300-patient cohort; each patient's data comes from its id, so loads are instant."""

import zlib

import matplotlib
import numpy as np

from .common import RC, axes_mm, figure

ARMS = ["A", "B", "C"]
STAGES = ["I", "II", "III", "IV"]
LINES = ["Chemo", "Targeted", "Immuno"]


def patients(n=300):
    """Patient ids with their arm and stage: ``[(id, arm, stage), ...]``."""
    rng = np.random.default_rng(7)
    return [(f"S{i:03d}", ARMS[rng.integers(3)], STAGES[rng.integers(4)]) for i in range(1, n + 1)]


def _data(patient):
    rng = np.random.default_rng(zlib.crc32(patient.encode()))
    starts = np.sort(rng.uniform(0, 30, 3))
    lines = [(LINES[i], s, s + rng.uniform(2, 8)) for i, s in enumerate(starts)]
    return lines, sorted(rng.uniform(0, 40, 4))


def timeline(w, h, patient="S001", arm="A", stage="I"):
    """Treatment lines and events of one patient, coloured by arm."""
    lines, events = _data(patient)
    with matplotlib.rc_context(RC):
        fig = figure(w, h)
        ax = axes_mm(fig, w, h, left=14, top=5, right=2, bottom=8)
        for i, (_, s, e) in enumerate(lines):
            ax.barh(i, e - s, left=s, height=0.6, color=f"C{ARMS.index(arm)}", alpha=0.5 + 0.2 * i)
        ax.set_yticks(range(len(lines)), [n for n, _, _ in lines])
        ax.vlines(events, -0.5, len(lines) - 0.5, color="0.2", lw=0.6, ls=":")
        ax.set(xlim=(0, 40), ylim=(-0.5, len(lines) - 0.5), xlabel="Months",
               title=f"{patient} · arm {arm} · stage {stage}")
        return fig
