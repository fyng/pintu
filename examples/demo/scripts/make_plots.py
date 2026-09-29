"""Draws the demo's static plots from synthetic data, at their board cell sizes."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "plots"
MM = 1 / 25.4
plt.rcParams.update({"font.size": 6, "svg.fonttype": "none", "pdf.fonttype": 42,
                     "axes.spines.top": False, "axes.spines.right": False})
rng = np.random.default_rng(0)


def fig(w, h):
    """A figure of w x h mm with a margin that keeps the 5 mm letter zone free."""
    f = plt.figure(figsize=(w * MM, h * MM))
    f.subplots_adjust(left=10 / w, bottom=8 / h, right=1 - 2 / w, top=1 - 6 / h)
    return f, f.add_subplot()


def main():
    OUT.mkdir(exist_ok=True)
    f, ax = fig(89, 55)
    x = np.linspace(0, 10, 200)
    for k in range(3):
        ax.plot(x, np.sin(x + k) * np.exp(-x / (5 + 3 * k)), lw=0.8, label=f"series {k + 1}")
    ax.set(xlabel="time", ylabel="signal")
    ax.legend(frameon=False)
    f.savefig(OUT / "lines.pdf")

    f, ax = fig(89, 55)
    pts = rng.normal(size=(300, 2)) @ np.array([[1, 0.6], [0, 0.8]])
    ax.scatter(*pts.T, s=2, lw=0)
    ax.set(xlabel="x", ylabel="y")
    f.savefig(OUT / "scatter.svg")

    f, ax = fig(58, 55)
    ax.bar(list("ABCDE"), rng.integers(5, 30, 5))
    ax.set(ylabel="count")
    f.savefig(OUT / "bars.pdf")

    f, ax = fig(120, 55)
    ax.imshow(rng.normal(size=(12, 30)).cumsum(1), aspect="auto", cmap="viridis")
    ax.set(xlabel="position", ylabel="sample")
    f.savefig(OUT / "heatmap.png", dpi=300)


if __name__ == "__main__":
    main()
