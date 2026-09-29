"""Shared figure setup for the fixture recipes."""

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure

MM = 25.4
RC = {
    "font.family": "DejaVu Sans",
    "font.size": 6,
    "axes.labelsize": 7,
    "axes.titlesize": 7,
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
    "legend.fontsize": 6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 0.5,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "lines.linewidth": 1.0,
}
matplotlib.rcParams.update(RC)

COLORS = ["#1b6ca8", "#e0812a", "#3a9d5d", "#c23b3b", "#7a5aa6", "#8c6d31"]


def panel_figure(w, h):
    """A constrained-layout figure of w x h mm."""
    return Figure(figsize=(w / MM, h / MM), layout="constrained")
