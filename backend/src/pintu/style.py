"""Hard-coded Nature preset (from academic-design-system, formats/publication).

Style packs (SPEC §10) replace this module in B2.
"""

NATURE = {
    "name": "nature",
    "widths": {"col1": 89.0, "col15": 136.0, "full": 183.0},
    "max_height": 170.0,
    "font": ["IBM Plex Sans", "Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
    "font_size_pt": 7.0,
    "letter": {"size_pt": 8.0, "lower": True, "band_mm": 3.5},
}


def get(name: str) -> dict:
    """The preset for a style name; every name maps to Nature for now."""
    return NATURE


def margins(name: str) -> dict:
    """Multiples layout in mm (SPEC §8) for a style name.

    Keys: outer ``left``, ``right``, ``top``, ``bottom``; ``gap`` between axes;
    ``tick``, added to a gap where inner tick labels show; ``title``, above each
    row of axes; ``key``, above the grid when there is a shared key.
    """
    return {"left": 12.0, "right": 1.5, "top": 1.0, "bottom": 7.5, "gap": 2.0, "tick": 4.0,
            "title": 3.0, "key": 3.5}
