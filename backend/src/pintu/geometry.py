"""Page grid geometry (SPEC §6).

A cell is ``(x0, y0, x1, y1)`` in grid lines. On an axis of length ``L`` with
``N`` units and gutter ``g``, the pitch is ``p = (L + g) / N``; the span between
lines ``a`` and ``b`` starts at ``a * p`` and is ``(b - a) * p - g`` long.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Optional, Sequence

Cell = tuple[int, int, int, int]


@dataclass(frozen=True)
class Page:
    """Page size in mm, grid units per axis and gutter in mm."""

    width: float = 183.0
    height: float = 170.0
    nx: int = 36
    ny: int = 36
    gutter: float = 3.0

    def rect(self, cell: Sequence[int]) -> tuple[float, float, float, float]:
        """Returns the cell's (x, y, w, h) in mm."""
        x0, y0, x1, y1 = cell
        x, w = span(self.width, self.nx, x0, x1, self.gutter)
        y, h = span(self.height, self.ny, y0, y1, self.gutter)
        return x, y, w, h


def pitch(length: float, n: int, gutter: float = 3.0) -> float:
    """Distance in mm between adjacent grid lines."""
    return (length + gutter) / n


def span(length: float, n: int, a: int, b: int, gutter: float = 3.0) -> tuple[float, float]:
    """Start and length in mm of the span between grid lines a and b."""
    p = pitch(length, n, gutter)
    return a * p, (b - a) * p - gutter


def even_span(length: float, n: int, i: int, k: int = 1, gutter: float = 3.0) -> tuple[float, float]:
    """Start and length in mm of k units from unit i, of n equal units with gutters between them."""
    u = (length - (n - 1) * gutter) / n
    return i * (u + gutter), k * u + (k - 1) * gutter


def snap(mm: float, length: float, n: int, gutter: float = 3.0) -> int:
    """Nearest grid line to a position in mm, clamped to [0, n]."""
    return max(0, min(n, round(mm / pitch(length, n, gutter))))


def valid_cell(cell: Sequence[int], page: Page) -> bool:
    """Whether a cell has positive size and lies within the page grid."""
    x0, y0, x1, y1 = cell
    return 0 <= x0 < x1 <= page.nx and 0 <= y0 < y1 <= page.ny


def overlaps(a: Sequence[int], b: Sequence[int]) -> bool:
    """Whether two cells share any area (touching edges do not count)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def find_overlaps(cells: dict[str, Sequence[int]]) -> list[tuple[str, str]]:
    """All pairs of ids whose cells overlap."""
    ids = list(cells)
    return [(p, q) for i, p in enumerate(ids) for q in ids[i + 1:] if overlaps(cells[p], cells[q])]


def reading_order(cells: dict[str, Sequence[int]]) -> list[str]:
    """Ids sorted top to bottom, then left to right, by top-left corner."""
    return sorted(cells, key=lambda k: (cells[k][1], cells[k][0]))


AUTO = object()
"""Letter setting: assign by reading order."""


def assign_letters(cells: dict[str, Sequence[int]], settings: dict[str, object]) -> dict[str, Optional[str]]:
    """Resolves panel letters.

    Args:
        cells: Panel id to cell.
        settings: Panel id to a fixed letter (str), ``None`` (no letter) or
            ``AUTO``. Missing ids are ``AUTO``.

    Returns:
        Panel id to letter or None. Auto panels take the next letter in reading
        order after the previous lettered panel, skipping fixed letters.
    """
    alphabet = string.ascii_lowercase
    fixed = {v.lower() for v in settings.values() if isinstance(v, str)}
    out: dict[str, Optional[str]] = {}
    nxt = 0
    for pid in reading_order(cells):
        s = settings.get(pid, AUTO)
        if s is None:
            out[pid] = None
        elif isinstance(s, str):
            out[pid] = s
            if s.lower() in alphabet:
                nxt = alphabet.index(s.lower()) + 1
        else:
            while nxt < len(alphabet) and alphabet[nxt] in fixed:
                nxt += 1
            out[pid] = alphabet[nxt] if nxt < len(alphabet) else None
            nxt += 1
    return out


def split(cell: Sequence[int], n: int, axis: str = "x") -> tuple[list[Cell], bool]:
    """Splits a cell into n parts along an axis.

    Args:
        cell: The cell to split.
        n: Number of parts, at least 1 and at most the cell's units on the axis.
        axis: "x" for side by side, "y" for stacked.

    Returns:
        The parts, and whether they are all equal (the split is exact).
    """
    x0, y0, x1, y1 = cell
    lo, hi = (x0, x1) if axis == "x" else (y0, y1)
    units = hi - lo
    if not 1 <= n <= units:
        raise ValueError(f"cannot split {units} units into {n}")
    edges = [lo + round(i * units / n) for i in range(n + 1)]
    parts = [
        (a, y0, b, y1) if axis == "x" else (x0, a, x1, b)
        for a, b in zip(edges, edges[1:])
    ]
    return parts, units % n == 0

