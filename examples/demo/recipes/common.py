"""Shared figure setup for the demo recipes."""

from matplotlib.figure import Figure

MM = 1 / 25.4
RC = {"font.size": 6, "axes.labelsize": 6, "xtick.labelsize": 5.5, "ytick.labelsize": 5.5,
      "legend.fontsize": 5.5, "axes.linewidth": 0.5, "xtick.major.width": 0.5, "ytick.major.width": 0.5,
      "lines.linewidth": 1.0, "axes.spines.top": False, "axes.spines.right": False}


def figure(w, h):
    """A figure of w × h mm; the caller lays out axes in mm with `axes_mm`."""
    return Figure(figsize=(w * MM, h * MM))


def axes_mm(fig, w, h, left, top, right, bottom):
    """Adds axes with margins in mm (top leaves room for the 5 mm letter zone)."""
    return fig.add_axes([left / w, bottom / h, (w - left - right) / w, (h - top - bottom) / h])
