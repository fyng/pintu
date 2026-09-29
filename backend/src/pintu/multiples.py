"""Multiples panels: mosaic parsing, cell sizes and reflow (SPEC §8).

A mosaic is a nested list of item values in ``Figure.subplot_mosaic`` form: a
repeated value spans cells (the cells must form a rectangle) and ``"."`` leaves
a cell empty. ``pintu_sdk.multiples`` lays the mosaic out the same way; this
module is the backend's copy, for validation and reflow.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Optional

EMPTY = "."
SHARE = ("all", "row", "col", "none")
DEFAULT_SHARE = {"x": "all", "y": "all"}

Span = tuple[int, int, int, int]  # (r0, c0, r1, c1), end exclusive


class MosaicError(ValueError):
    """An invalid mosaic, share or ratios."""


def parse(mosaic) -> tuple[int, int, dict[str, Span]]:
    """Rows, columns and each item's span, in reading order of first cell.

    Raises:
        MosaicError: Not a non-empty rectangular list of lists, or an item's
            cells do not form a rectangle.
    """
    if not isinstance(mosaic, list) or not mosaic or not all(isinstance(r, list) and r for r in mosaic):
        raise MosaicError("mosaic must be a non-empty list of non-empty rows")
    ncols = len(mosaic[0])
    if any(len(r) != ncols for r in mosaic):
        raise MosaicError("mosaic rows must have the same length")
    cells: dict[str, list[tuple[int, int]]] = {}
    for r, row in enumerate(mosaic):
        for c, v in enumerate(row):
            if isinstance(v, (list, dict)):
                raise MosaicError("nested mosaics are not supported")
            v = str(v)
            if v != EMPTY:
                cells.setdefault(v, []).append((r, c))
    spans = {}
    for v, rc in cells.items():
        rs, cs = [r for r, _ in rc], [c for _, c in rc]
        span = (min(rs), min(cs), max(rs) + 1, max(cs) + 1)
        if (span[2] - span[0]) * (span[3] - span[1]) != len(rc):
            raise MosaicError(f"mosaic item {v!r} does not span a rectangle")
        spans[v] = span
    return len(mosaic), ncols, spans


def share_of(share) -> dict[str, str]:
    """Share modes for x and y from a mapping ``{x, y}`` or one mode for both; missing keys default."""
    if share is None:
        return dict(DEFAULT_SHARE)
    if isinstance(share, str):
        share = {"x": share, "y": share}
    if not isinstance(share, dict) or set(share) - {"x", "y"}:
        raise MosaicError("share must be a mode or {x: mode, y: mode}")
    out = {**DEFAULT_SHARE, **{k: str(v) for k, v in share.items()}}
    for k, v in out.items():
        if v not in SHARE:
            raise MosaicError(f"share {k} must be one of {', '.join(SHARE)}, got {v!r}")
    return out


def validate(m: dict) -> None:
    """Checks a board's ``source.multiples`` mapping.

    Raises:
        MosaicError: A key is missing or invalid.
    """
    if not isinstance(m, dict):
        raise MosaicError("multiples must be a mapping")
    if not isinstance(m.get("item"), str) or not m["item"]:
        raise MosaicError("multiples needs an item param name")
    nrows, ncols, _ = parse(m.get("mosaic"))
    share_of(m.get("share"))
    for key, n in (("width_ratios", ncols), ("height_ratios", nrows)):
        v = m.get(key)
        if v is None:
            continue
        if not (isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            and x > 0 for x in v)):
            raise MosaicError(f"{key} must be {n} positive numbers")


def call_kwargs(m: dict) -> dict:
    """The keyword arguments a multiples recipe takes: mosaic (as strings), share and any ratios."""
    out = {"mosaic": [[str(v) for v in row] for row in m["mosaic"]], "share": share_of(m.get("share"))}
    for key in ("width_ratios", "height_ratios"):
        if m.get(key) is not None:
            out[key] = [float(x) for x in m[key]]
    return out


def cell_size(w: float, h: float, nrows: int, ncols: int, margins: dict, share: dict) -> tuple[float, float]:
    """Mean width and height in mm of one mosaic cell's axes in a (w, h) panel, as the SDK lays it out."""
    gx = margins["gap"] + (0 if share["y"] in ("all", "row") else margins["tick"])
    gy = margins["gap"] + margins["title"] + (0 if share["x"] in ("all", "col") else margins["tick"])
    aw = w - margins["left"] - margins["right"] - (ncols - 1) * gx
    ah = h - margins["top"] - margins["title"] - margins["bottom"] - (nrows - 1) * gy
    return aw / ncols, ah / nrows


def reflow(m: dict, w: float, h: float, min_cell: Optional[tuple[float, float]], margins: dict) -> Optional[list]:
    """A new mosaic for a panel whose cells fall below ``min_cell``, or None if none is needed or better.

    Items keep their reading order, one cell each. Of the grids with room for
    every item, the pick is the one whose cells meet ``min_cell`` with the
    fewest empty cells (ties: cell aspect closest to ``min_cell``'s); if none
    meets it, the one closest to it. None if the pick is no better than now.
    """
    if not min_cell:
        return None
    nrows, ncols, spans = parse(m["mosaic"])
    share = share_of(m.get("share"))
    mw, mh = min_cell
    cw, ch = cell_size(w, h, nrows, ncols, margins, share)
    if cw >= mw and ch >= mh:
        return None
    items = list(spans)
    n = len(items)
    if n == 0:
        return None

    def score(r: int, c: int):
        cw, ch = cell_size(w, h, r, c, margins, share)
        fit = min(cw / mw, ch / mh)
        return (0, r * c - n, abs(math.log((cw / ch) / (mw / mh))) if cw > 0 and ch > 0 else math.inf) \
            if fit >= 1 else (1, -fit, 0)

    grids = [(math.ceil(n / c), c) for c in range(1, n + 1)]
    r, c = min(grids, key=lambda g: score(*g))
    if score(r, c) >= score(nrows, ncols):
        return None
    flat = items + [EMPTY] * (r * c - n)
    return [flat[i * c:(i + 1) * c] for i in range(r)]


def recipe_meta(path: Optional[Path], name: str) -> Optional[dict]:
    """``item`` and ``min_cell`` of a ``@multiples``-decorated recipe, read with ``ast``; None otherwise.

    The item param is the function's second parameter (after ``ax``);
    ``min_cell`` must be a literal.
    """
    if path is None or "." in name:
        return None
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != name:
            continue
        for dec in node.decorator_list:
            f = dec.func if isinstance(dec, ast.Call) else None
            fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if fname != "multiples":
                continue
            args = node.args.posonlyargs + node.args.args
            meta = {"item": args[1].arg if len(args) > 1 else None, "min_cell": None}
            for kw in dec.keywords:
                if kw.arg == "min_cell":
                    try:
                        v = ast.literal_eval(kw.value)
                        meta["min_cell"] = [float(v[0]), float(v[1])]
                    except (ValueError, TypeError, IndexError):
                        pass
            return meta
    return None
