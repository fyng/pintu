"""Render lint against the style pack (SPEC §8).

``check`` runs on a render summary (``worker.summarize``), so it costs no
render and no Typst compile. Each issue is ``{"rule", "message"}``; ``lines``
gives the ``"rule: message"`` text form the agent reads.
"""

from __future__ import annotations

import re
from typing import Optional

from . import style as styles

EDGE_TOL_MM = 0.3
_NUMBER = re.compile(r"^[−\-+]?[\d.,]+(e[−\-+]?\d+)?%?$")


def fmt_size(w: float, h: float) -> str:
    return f"{w:g} x {h:g} mm"


def drawn_texts(summary: dict) -> list[dict]:
    """Summary texts without tick labels that matplotlib does not draw.

    The summary lists tick labels outside the view limits too. A numeric label is
    kept only if it sits beside some axes, level with its span.
    """
    boxes = [a["bbox_mm"] for a in summary["axes"]]
    out = []
    for t in summary["texts"]:
        x0, y0, x1, y1 = t["bbox_mm"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if _NUMBER.match(t["text"].strip()) and boxes and not any(
                (b[1] - 0.1 <= cy <= b[3] + 0.1 and (cx < b[0] or cx > b[2]))
                or (b[0] - 0.1 <= cx <= b[2] + 0.1 and (cy > b[3] or cy < b[1])) for b in boxes):
            continue
        out.append(t)
    return out


def values(summary: dict, prop: str, texts: Optional[list] = None) -> list[tuple[float, str]]:
    """(value, what) pairs of a ``style.PROPERTIES`` property in a render summary."""
    texts = drawn_texts(summary) if texts is None else texts
    if prop == "text_pt":
        return [(t["fontsize_pt"], f"text {t['text']!r}") for t in texts]
    if prop == "tick_label_pt":
        return [(t["fontsize_pt"], f"tick label {t['text']!r}") for t in texts if t.get("role") == "tick"]
    what = styles.PROPERTIES[prop].split(",")[0]
    return [(v, what) for v in summary.get(prop) or []]


def check(summary: Optional[dict], w: float, h: float, pack: Optional[styles.StylePack] = None) -> list[dict]:
    """Lint issues of a render at cell size (w, h) under a style pack.

    Built-in rules: ``size`` (figure size within ``[lint] size_tol_mm``),
    ``overflow`` (text outside the figure), ``font`` (text outside ``[lint]
    text_pt``, or set in a font Typst cannot find), then the pack's
    ``[[lint.rules]]``, one issue per rule.
    """
    if not summary:
        return []
    pack = pack or styles.default()
    out = []
    sw, sh = summary["size_mm"]
    tol = pack.size_tol_mm
    if abs(sw - w) > tol or abs(sh - h) > tol:
        out.append({"rule": "size", "message": f"figure is {fmt_size(sw, sh)}, cell is {fmt_size(w, h)}"})
    lo, hi = pack.text_pt
    e = EDGE_TOL_MM
    texts = drawn_texts(summary)
    for t in texts:
        x0, y0, x1, y1 = t["bbox_mm"]
        if x0 < -e or y0 < -e or x1 > sw + e or y1 > sh + e:
            out.append({"rule": "overflow", "message": f"text {t['text']!r} at {t['bbox_mm']} falls outside the figure"})
        if not lo - 0.01 <= t["fontsize_pt"] <= hi + 0.01:
            out.append({"rule": "font", "message": f"text {t['text']!r} is {t['fontsize_pt']:g} pt (allowed {lo:g}-{hi:g} pt)"})
    typst_fonts = styles.typst_families(pack.font_paths)
    for f in summary.get("fonts") or []:
        if f.lower() not in typst_fonts:
            out.append({"rule": "font", "message": f"text is set in {f!r}, which Typst cannot find; the board shows "
                                                   "it in another font (install it or add it to the pack's [fonts] paths)"})
    for r in pack.rules:
        bad = [(v, what) for v, what in values(summary, r.property, texts)
               if (r.min is not None and v < r.min - 0.01) or (r.max is not None and v > r.max + 0.01)]
        if bad:
            low = [b for b in bad if r.min is not None and b[0] < r.min - 0.01]
            v, what = min(low) if low else max(bad)
            n = f"{len(bad)} values; " if len(bad) > 1 else ""
            out.append({"rule": r.id, "message": f"{n}{what} is {v:g} pt (allowed {r.allowed()})"
                                                 + (f": {r.message}" if r.message else "")})
    return out


def lines(issues: list[dict]) -> list[str]:
    """Issues as ``"rule: message"`` lines."""
    return [f"{i['rule']}: {i['message']}" for i in issues]
