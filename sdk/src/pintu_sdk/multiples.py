"""``choice`` and ``@multiples``: one recipe function drawn once per mosaic cell (SPEC §8).

matplotlib is imported only when a panel renders, so the SDK stays dependency-free.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
from typing import Any, Callable, Optional, Sequence

EMPTY = "."
SHARE = ("all", "row", "col", "none")
MM = 1 / 25.4
MARGINS_ENV = "PINTU_MARGINS"
"""JSON margins pintu's render worker sets from the style pack."""
MARGINS = {"left": 12.0, "right": 1.5, "top": 1.0, "bottom": 9.0, "gap": 2.0, "tick": 4.0,
           "title": 3.0, "key": 3.5}
"""Margins in mm used outside pintu (same keys as ``pintu.style.margins``)."""


class choice:
    """A param whose values come from a function, e.g. ``choice(list_patients)``.

    Args:
        values: A function returning the values, or a sequence of them.
    """

    def __init__(self, values: Callable[[], Sequence] | Sequence):
        self._values = values

    def values(self) -> list:
        """The allowed values."""
        return list(self._values() if callable(self._values) else self._values)


def parse_mosaic(mosaic: Sequence[Sequence]) -> tuple[int, int, dict[str, tuple[int, int, int, int]]]:
    """Rows, columns and each item's ``(r0, c0, r1, c1)`` span (end exclusive), in reading order.

    Raises:
        ValueError: Rows differ in length, or an item's cells are not a rectangle.
    """
    rows = [list(r) for r in mosaic]
    if not rows or not rows[0] or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError("mosaic must be a non-empty list of rows of equal length")
    cells: dict[str, list[tuple[int, int]]] = {}
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if str(v) != EMPTY:
                cells.setdefault(str(v), []).append((r, c))
    spans = {}
    for v, rc in cells.items():
        rs, cs = [r for r, _ in rc], [c for _, c in rc]
        span = (min(rs), min(cs), max(rs) + 1, max(cs) + 1)
        if (span[2] - span[0]) * (span[3] - span[1]) != len(rc):
            raise ValueError(f"mosaic item {v!r} does not span a rectangle")
        spans[v] = span
    return len(rows), len(rows[0]), spans


def _share(share) -> dict[str, str]:
    if share is None:
        share = "all"
    if isinstance(share, str):
        share = {"x": share, "y": share}
    out = {"x": share.get("x", "all"), "y": share.get("y", "all")}
    for k, v in out.items():
        if v not in SHARE:
            raise ValueError(f"share {k} must be one of {SHARE}, got {v!r}")
    return out


def margins() -> dict:
    """The margins in mm: pintu's style pack when rendering in pintu, else ``MARGINS``."""
    return {**MARGINS, **json.loads(os.environ.get(MARGINS_ENV) or "{}")}


def _edges(n: int, lo: float, length: float, gap: float, ratios: Optional[Sequence[float]]) -> list[tuple[float, float]]:
    """Start and length of n tracks from ``lo`` across ``length`` with gaps between them."""
    ratios = list(ratios) if ratios else [1.0] * n
    if len(ratios) != n:
        raise ValueError(f"expected {n} ratios, got {len(ratios)}")
    unit = (length - (n - 1) * gap) / sum(ratios)
    out, at = [], lo
    for r in ratios:
        out.append((at, r * unit))
        at += r * unit + gap
    return out


def layout(w: float, h: float, mosaic, share=None, width_ratios=None, height_ratios=None,
           m: Optional[dict] = None, key: bool = False) -> dict[str, tuple[float, float, float, float]]:
    """Each item's axes rect ``(x, y, w, h)`` in mm from the top-left of a (w, h) panel.

    Args:
        w: Panel width in mm.
        h: Panel height in mm.
        mosaic: The mosaic.
        share: Share modes.
        width_ratios: Relative column widths.
        height_ratios: Relative row heights.
        m: Margins; default ``margins()``.
        key: Whether to reserve room for a shared key at the top.
    """
    m = m or margins()
    nrows, ncols, spans = parse_mosaic(mosaic)
    s = _share(share)
    gx = m["gap"] + (0 if s["y"] in ("all", "row") else m["tick"])
    gy = m["gap"] + m["title"] + (0 if s["x"] in ("all", "col") else m["tick"])
    top = m["top"] + m["title"] + (m["key"] if key else 0)
    cols = _edges(ncols, m["left"], w - m["left"] - m["right"], gx, width_ratios)
    rows = _edges(nrows, top, h - top - m["bottom"], gy, height_ratios)
    out = {}
    for v, (r0, c0, r1, c1) in spans.items():
        x, y = cols[c0][0], rows[r0][0]
        out[v] = (x, y, cols[c1 - 1][0] + cols[c1 - 1][1] - x, rows[r1 - 1][0] + rows[r1 - 1][1] - y)
    return out


def _outer(spans: dict, v: str) -> tuple[bool, bool]:
    """Whether an item's axes are on the bottom and on the left outer edge (no axes beside them)."""
    r0, c0, r1, c1 = spans[v]

    def taken(r: int, c: int) -> bool:
        return any(q != v and s[0] <= r < s[2] and s[1] <= c < s[3] for q, s in spans.items())

    return (not any(taken(r1, c) for c in range(c0, c1)),
            not any(taken(r, c0 - 1) for r in range(r0, r1)))


def multiples(item: Optional[choice] = None, min_cell: Optional[tuple[float, float]] = None,
              rc: Optional[dict] = None) -> Callable:
    """Makes a recipe from a function that draws one item on one axes.

    The function takes ``(ax, <item>, **params)``; its second parameter names the
    item. The recipe takes ``(w, h, mosaic=None, share=None, width_ratios=None,
    height_ratios=None, **params)`` and returns one Figure: margins from the style
    pack, axes shared per ``share`` (``all``, ``row``, ``col``, ``none``; one mode
    or ``{x, y}``), axis labels and shared tick labels on the outer edges only, and
    one key from every axes' legend handles. Without a mosaic, it draws the item
    passed by name (e.g. ``patient=...``) as a 1 × 1 mosaic.

    Args:
        item: The item's values.
        min_cell: Smallest useful axes (w, h) in mm; below it pintu offers a reflow.
        rc: matplotlib rcParams to draw under.
    """

    def wrap(fn: Callable) -> Callable:
        name = list(inspect.signature(fn).parameters)[1]

        @functools.wraps(fn)
        def recipe(w, h, mosaic=None, share=None, width_ratios=None, height_ratios=None, **params):
            import matplotlib
            from matplotlib.figure import Figure

            if mosaic is None:
                one = params.pop(name, None)
                if one is None:
                    vals = item.values() if item else []
                    if not vals:
                        raise ValueError(f"no mosaic and no {name!r} given")
                    one = vals[0]
                mosaic = [[one]]
            mosaic = [[str(v) for v in row] for row in mosaic]
            s = _share(share)
            m = margins()
            _, _, spans = parse_mosaic(mosaic)
            with matplotlib.rc_context(rc or {}):
                fig = Figure(figsize=(w * MM, h * MM))
                rects = layout(w, h, mosaic, s, width_ratios, height_ratios, m)
                axes: dict[str, Any] = {}
                first: dict[tuple[str, str, int], Any] = {}
                for v, (x, y, aw, ah) in rects.items():
                    r0, c0 = spans[v][0], spans[v][1]
                    kw = {}
                    for ax_name, mode in (("x", s["x"]), ("y", s["y"])):
                        group = {"all": 0, "row": r0, "col": c0}.get(mode)
                        if group is not None and (ax_name, mode, group) in first:
                            kw[f"share{ax_name}"] = first[(ax_name, mode, group)]
                    ax = fig.add_axes((x / w, 1 - (y + ah) / h, aw / w, ah / h), **kw)
                    for ax_name, mode in (("x", s["x"]), ("y", s["y"])):
                        group = {"all": 0, "row": r0, "col": c0}.get(mode)
                        if group is not None:
                            first.setdefault((ax_name, mode, group), ax)
                    axes[v] = ax
                    fn(ax, v, **params)
                handles: dict[str, Any] = {}
                for v, ax in axes.items():
                    bottom, left = _outer(spans, v)
                    if not bottom:
                        ax.set_xlabel("")
                        if s["x"] in ("all", "col"):
                            ax.tick_params(axis="x", labelbottom=False)
                    if not left:
                        ax.set_ylabel("")
                        if s["y"] in ("all", "row"):
                            ax.tick_params(axis="y", labelleft=False)
                    for hd, lb in zip(*ax.get_legend_handles_labels()):
                        if not lb.startswith("_"):
                            handles.setdefault(lb, hd)
                    if ax.get_legend():
                        ax.get_legend().remove()
                if handles:
                    rects = layout(w, h, mosaic, s, width_ratios, height_ratios, m, key=True)
                    for v, (x, y, aw, ah) in rects.items():
                        axes[v].set_position((x / w, 1 - (y + ah) / h, aw / w, ah / h))
                    fig.legend(list(handles.values()), list(handles), loc="upper center", ncols=len(handles),
                               frameon=False, bbox_to_anchor=(0.5, 1 - m["top"] / h), borderaxespad=0,
                               handlelength=1.2, columnspacing=1.0)
            return fig

        recipe.__pintu_multiples__ = {"item": name, "values": item, "min_cell": min_cell}
        return recipe

    return wrap
