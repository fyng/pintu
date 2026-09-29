"""Timelines for a synthetic 300-patient cohort; each patient's data comes from its id, so loads are instant.

``timeline`` is a multiples recipe: it draws one patient on one axes, and a
multiples panel lays several patients out from its mosaic.
"""

import zlib

import numpy as np
from pintu_sdk import choice, multiples

from .common import RC

ARMS = ["A", "B", "C"]
STAGES = ["I", "II", "III", "IV"]
LINES = ["Chemo", "Targeted", "Immuno"]


def patients(n=300):
    """Patient ids with their arm and stage: ``[(id, arm, stage), ...]``."""
    rng = np.random.default_rng(7)
    return [(f"S{i:03d}", ARMS[rng.integers(3)], STAGES[rng.integers(4)]) for i in range(1, n + 1)]


def patient_ids():
    return [p for p, _, _ in patients()]


def _data(patient):
    rng = np.random.default_rng(zlib.crc32(patient.encode()))
    starts = np.sort(rng.uniform(0, 30, 3))
    lines = [(LINES[i], s, s + rng.uniform(2, 8)) for i, s in enumerate(starts)]
    return lines, sorted(rng.uniform(0, 40, 4))


@multiples(item=choice(patient_ids), min_cell=(40, 16), rc=RC)
def timeline(ax, patient, arm=None, stage=None):
    """Treatment lines and events of one patient, coloured by arm."""
    if arm is None or stage is None:
        _, arm, stage = next(p for p in patients() if p[0] == patient)
    lines, events = _data(patient)
    for i, (_, s, e) in enumerate(lines):
        ax.barh(i, e - s, left=s, height=0.6, color=f"C{ARMS.index(arm)}", alpha=0.5 + 0.2 * i,
                label=f"Arm {arm}" if i == 0 else None)
    ax.set_yticks(range(len(lines)), [n for n, _, _ in lines])
    ax.vlines(events, -0.5, len(lines) - 0.5, color="0.2", lw=0.6, ls=":")
    ax.set(xlim=(0, 40), ylim=(-0.5, len(lines) - 0.5), xlabel="Months")
    ax.set_title(f"{patient} · stage {stage}", fontsize=6, pad=2)
