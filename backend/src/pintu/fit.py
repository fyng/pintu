"""How a static file fills its panel: natural size, contain fit, and a PNG as placed (SPEC §6).

The board fits a static file into the area below the panel's letter band and keeps its
aspect ratio (``fit: "contain"``, centred), so a file drawn at another aspect leaves
part of the area empty.
"""

from __future__ import annotations

import re
import threading
from typing import Optional

import typst

from .codegen import typst_str
from .project import PathError, Project

PT_MM = 25.4 / 72
FILL_MIN = 0.95  # below this share of the area on either axis, the fit is a problem

_sizes: dict[tuple[str, int], Optional[tuple[float, float]]] = {}
_lock = threading.Lock()


def _first(out) -> bytes:
    return out[0] if isinstance(out, list) else out


def natural_size(project: Project, rel: str) -> Optional[tuple[float, float]]:
    """(w, h) in mm of a PDF, SVG or raster file as Typst places it, or None if it does not load.

    Cached by path and modification time.
    """
    try:
        path = project.resolve(rel)
        key = (rel, path.stat().st_mtime_ns)
    except (PathError, OSError):
        return None
    with _lock:
        if key in _sizes:
            return _sizes[key]
    doc = f"#set page(width: auto, height: auto, margin: 0pt)\n#image({typst_str('/' + rel)})\n"
    try:
        svg = _first(typst.compile(input=doc.encode(), root=str(project.root), format="svg"))
        m = re.search(rb'width="([\d.]+)pt"\s+height="([\d.]+)pt"', svg[:400])
        size = (round(float(m[1]) * PT_MM, 2), round(float(m[2]) * PT_MM, 2)) if m else None
    except Exception:
        size = None
    with _lock:
        _sizes[key] = size
    return size


def contain(natural: tuple[float, float], area: tuple[float, float]) -> dict:
    """The contain fit of a file of ``natural`` size into ``area`` (both (w, h) in mm).

    Returns:
        ``natural_mm``, ``area_mm``, ``drawn_mm`` (the file's size as drawn) and ``fill``
        (drawn / area per axis, 0 to 1).
    """
    (nw, nh), (aw, ah) = natural, area
    s = min(aw / nw, ah / nh) if nw > 0 and nh > 0 else 0.0
    dw, dh = nw * s, nh * s
    return {"natural_mm": [round(nw, 2), round(nh, 2)], "area_mm": [round(aw, 2), round(ah, 2)],
            "drawn_mm": [round(dw, 2), round(dh, 2)],
            "fill": [round(dw / aw, 3) if aw else 0.0, round(dh / ah, 3) if ah else 0.0]}


def panel_fit(project: Project, board, panel: dict) -> Optional[dict]:
    """The contain fit of a static-file panel's source into its area below the letter band, or None."""
    src = panel.get("source") or {}
    if "file" not in src:
        return None
    size = natural_size(project, str(src["file"]))
    if size is None:
        return None
    _, _, w, h = board.content_rect(panel)
    return {"file": str(src["file"]), **contain(size, (w, h))}


def problem(fit: Optional[dict]) -> Optional[str]:
    """How much of the plot area a fit leaves empty (e.g. "51% of the plot area's height is
    empty"), or None if it fills the area."""
    if not fit or min(fit["fill"]) >= FILL_MIN:
        return None
    axis = 1 if fit["fill"][1] < fit["fill"][0] else 0
    return f"{1 - fit['fill'][axis]:.0%} of the plot area's {('width', 'height')[axis]} is empty"


def placed_png(project: Project, rel: str, area: tuple[float, float], max_px: int = 1024) -> bytes:
    """PNG of a static file fitted into an area of (w, h) mm as the board draws it, with the
    area's edge dashed so empty space shows."""
    w, h = area
    doc = (f"#set page(width: {w:.3f}mm, height: {h:.3f}mm, margin: 0pt, fill: white)\n"
           f"#place(image({typst_str('/' + rel)}, width: 100%, height: 100%, fit: \"contain\"))\n"
           "#place(rect(width: 100%, height: 100%, stroke: (paint: luma(150), thickness: 0.4pt, dash: \"dashed\")))\n")
    ppi = min(300.0, max_px / (max(w, h) / 25.4))
    return _first(typst.compile(input=doc.encode(), root=str(project.root), format="png", ppi=ppi))
