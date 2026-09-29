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
