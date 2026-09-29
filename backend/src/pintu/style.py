"""Style packs: journal presets, letters, fonts and lint rules (SPEC §10).

A style pack is a folder with ``stylepack.toml``. pintu ships a neutral
``default`` pack in ``pintu/stylepacks/``; ``[style] pack`` in pintu.toml picks
another by built-in name or by a path relative to the project root. A board's
``page.style`` picks a preset in the pack; an unknown name gives the pack's
default preset.

Schema (every table but ``[pack]`` and ``[presets.*]`` is optional)::

    [pack]
    name = "strict"                  # required
    default_preset = "nature"        # required; a key of [presets]
    notes = ["..."]                  # extra rules for the LLM, one line each

    [fonts]
    family = ["IBM Plex Sans", ...]  # preference order, for Typst text and lint
    paths = ["fonts"]                # font folders relative to the pack; given to
                                     # Typst and registered in the recipe kernel
    size_pt = 7                      # board text size

    [letter]
    size_pt = 8
    weight = 700                     # 100-900, or "regular" / "bold"
    case = "lower"                   # "lower", "upper" or "keep"
    band_mm = 3.5                    # letter band height (SPEC §6)

    [margins]                        # mm; panel margins for helpers such as multiples
    left = 12                        # also right, top, bottom, gap, tick, title, key

    [presets.nature]
    widths = {col1 = 89, full = 183} # mm
    max_height = 170                 # mm
    letter = {case = "upper"}        # optional overrides of [letter]
    margins = {left = 12}            # optional overrides of [margins]

    [lint]
    text_pt = [5, 7]                 # allowed size of every drawn text
    size_tol_mm = 0.1                # render size tolerance

    [[lint.rules]]                   # extra rules: bounds on a property of the render
    id = "tick-labels"               # optional; defaults to the property
    property = "tick_label_pt"       # a key of PROPERTIES
    min = 5                          # min and/or max
    message = "ticks are 5 pt"       # optional hint shown with a violation

    [typst]
    snippet = "#set text(...)"       # inserted after the board's page setup
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

FILE = "stylepack.toml"
BUILTIN = Path(__file__).with_name("stylepacks")
DEFAULT = "default"

PROPERTIES = {
    "text_pt": "size of any drawn text, pt",
    "tick_label_pt": "size of drawn tick labels, pt",
    "tick_length_pt": "length of visible tick marks, pt",
    "tick_width_pt": "line width of visible tick marks, pt",
    "line_width_pt": "line width of plotted lines, pt",
    "spine_width_pt": "line width of visible axes spines, pt",
}
"""Render properties a ``[[lint.rules]]`` entry may bound, with what they measure."""

WEIGHTS = {"regular": 400, "medium": 500, "bold": 700}
CASES = ("lower", "upper", "keep")
MARGIN_KEYS = ("left", "right", "top", "bottom", "gap", "tick", "title", "key")
_TOP = {"pack", "fonts", "letter", "margins", "presets", "lint", "typst"}


class StylePackError(ValueError):
    """A style pack that is missing or does not match the schema."""


@dataclass(frozen=True)
class Rule:
    """A bound on one render property (``[[lint.rules]]``)."""

    id: str
    property: str
    min: Optional[float] = None
    max: Optional[float] = None
    message: str = ""

    def allowed(self) -> str:
        """The bound as text, e.g. "5-7 pt" or ">= 5 pt"."""
        if self.min is not None and self.max is not None:
            return f"{self.min:g}-{self.max:g} pt"
        return f">= {self.min:g} pt" if self.min is not None else f"<= {self.max:g} pt"


@dataclass(frozen=True)
class StylePack:
    """A loaded style pack. See the module docstring for the file schema."""

    name: str
    root: Optional[Path]
    presets: dict
    default_preset: str
    fonts: tuple = ()
    font_paths: tuple = ()
    font_size_pt: float = 7.0
    letter: dict = field(default_factory=dict)
    margins: dict = field(default_factory=dict)
    text_pt: tuple = (5.0, 7.0)
    size_tol_mm: float = 0.1
    rules: tuple = ()
    typst: str = ""
    notes: tuple = ()

    def preset(self, name: Optional[str]) -> dict:
        """The preset dict for a board's ``page.style``; unknown names give the default preset."""
        key = name if name in self.presets else self.default_preset
        return self.presets[key]


def _num(v, where: str, lo: float = 0.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo:
        raise StylePackError(f"{where} must be a number >= {lo:g}, got {v!r}")
    return float(v)


def _table(doc: dict, key: str, where: str, allowed: set) -> dict:
    t = doc.get(key, {})
    if not isinstance(t, dict):
        raise StylePackError(f"{where}: [{key}] must be a table")
    extra = set(t) - allowed
    if extra:
        raise StylePackError(f"{where}: unknown key(s) in [{key}]: {', '.join(sorted(extra))}")
    return t


def _letter(t: dict, where: str, base: dict) -> dict:
    out = dict(base)
    if "size_pt" in t:
        out["size_pt"] = _num(t["size_pt"], f"{where} size_pt")
    if "weight" in t:
        w = t["weight"]
        w = WEIGHTS.get(w, w) if isinstance(w, str) else w
        if isinstance(w, bool) or not isinstance(w, int) or not 100 <= w <= 900:
            raise StylePackError(f"{where} weight must be 100-900 or one of {', '.join(WEIGHTS)}, got {t['weight']!r}")
        out["weight"] = w
    if "case" in t:
        if t["case"] not in CASES:
            raise StylePackError(f"{where} case must be one of {', '.join(CASES)}, got {t['case']!r}")
        out["case"] = t["case"]
    if "band_mm" in t:
        out["band_mm"] = _num(t["band_mm"], f"{where} band_mm")
    out["lower"] = out["case"] == "lower"
    out["upper"] = out["case"] == "upper"
    return out


def _margins(t: dict, where: str, base: dict) -> dict:
    out = dict(base)
    for k in t:
        if k not in MARGIN_KEYS:
            raise StylePackError(f"{where}: unknown margin {k!r} (use {', '.join(MARGIN_KEYS)})")
        out[k] = _num(t[k], f"{where} {k}")
    return out


def _rule(r, i: int, where: str) -> Rule:
    at = f"{where} [[lint.rules]] #{i + 1}"
    if not isinstance(r, dict):
        raise StylePackError(f"{at} must be a table")
    extra = set(r) - {"id", "property", "min", "max", "message"}
    if extra:
        raise StylePackError(f"{at}: unknown key(s): {', '.join(sorted(extra))}")
    prop = r.get("property")
    if prop not in PROPERTIES:
        raise StylePackError(f"{at}: property must be one of {', '.join(PROPERTIES)}, got {prop!r}")
    lo = _num(r["min"], f"{at} min") if "min" in r else None
    hi = _num(r["max"], f"{at} max") if "max" in r else None
    if lo is None and hi is None:
        raise StylePackError(f"{at}: give min and/or max")
    if lo is not None and hi is not None and lo > hi:
        raise StylePackError(f"{at}: min {lo:g} is above max {hi:g}")
    return Rule(id=str(r.get("id") or prop), property=prop, min=lo, max=hi, message=str(r.get("message", "")))


def parse(doc: dict, root: Optional[Path] = None, where: str = FILE) -> StylePack:
    """Validates a parsed ``stylepack.toml``.

    Args:
        doc: The parsed TOML.
        root: The pack folder; font paths resolve against it.
        where: Name used in error messages.

    Raises:
        StylePackError: The document does not match the schema.
    """
    extra = set(doc) - _TOP
    if extra:
        raise StylePackError(f"{where}: unknown table(s): {', '.join(sorted(extra))}")
    pack = _table(doc, "pack", where, {"name", "default_preset", "notes"})
    fonts = _table(doc, "fonts", where, {"family", "paths", "size_pt"})
    lint = _table(doc, "lint", where, {"text_pt", "size_tol_mm", "rules"})
    typ = _table(doc, "typst", where, {"snippet"})
    if not isinstance(pack.get("name"), str) or not pack["name"]:
        raise StylePackError(f"{where}: [pack] name is required")
    family = fonts.get("family", ["DejaVu Sans"])
    if not isinstance(family, list) or not family or not all(isinstance(f, str) and f for f in family):
        raise StylePackError(f"{where}: [fonts] family must be a non-empty list of names")
    paths = fonts.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise StylePackError(f"{where}: [fonts] paths must be a list of folders")
    font_paths = []
    for p in paths:
        fp = (root / p) if root else Path(p)
        if not fp.is_dir():
            raise StylePackError(f"{where}: font folder not found: {p}")
        font_paths.append(fp.resolve())
    letter = _letter(_table(doc, "letter", where, {"size_pt", "weight", "case", "band_mm"}), f"{where} [letter]",
                     {"size_pt": 8.0, "weight": 700, "case": "lower", "band_mm": 3.5})
    margins = _margins(_table(doc, "margins", where, set(MARGIN_KEYS)), f"{where} [margins]",
                       {"left": 12.0, "right": 1.5, "top": 1.0, "bottom": 9.0, "gap": 2.0, "tick": 4.0,
                        "title": 3.0, "key": 3.5})
    text_pt = lint.get("text_pt", [5, 7])
    if not isinstance(text_pt, list) or len(text_pt) != 2:
        raise StylePackError(f"{where}: [lint] text_pt must be [min, max]")
    lo, hi = (_num(v, f"{where} [lint] text_pt") for v in text_pt)
    if lo > hi:
        raise StylePackError(f"{where}: [lint] text_pt min {lo:g} is above max {hi:g}")
    rules = lint.get("rules", [])
    if not isinstance(rules, list):
        raise StylePackError(f"{where}: [lint] rules must be an array of tables ([[lint.rules]])")
    size_pt = _num(fonts.get("size_pt", 7), f"{where} [fonts] size_pt")
    presets = doc.get("presets")
    if not isinstance(presets, dict) or not presets:
        raise StylePackError(f"{where}: at least one [presets.<name>] table is required")
    out = {}
    for name, p in presets.items():
        at = f"{where} [presets.{name}]"
        if not isinstance(p, dict):
            raise StylePackError(f"{at} must be a table")
        bad = set(p) - {"widths", "max_height", "letter", "margins"}
        if bad:
            raise StylePackError(f"{at}: unknown key(s): {', '.join(sorted(bad))}")
        widths = p.get("widths")
        if not isinstance(widths, dict) or not widths:
            raise StylePackError(f"{at}: widths must be a table of name = mm")
        if "max_height" not in p:
            raise StylePackError(f"{at}: max_height is required")
        out[name] = {
            "name": name,
            "widths": {k: _num(v, f"{at} widths.{k}") for k, v in widths.items()},
            "max_height": _num(p["max_height"], f"{at} max_height"),
            "font": list(family),
            "font_size_pt": size_pt,
            "letter": _letter(p.get("letter", {}), f"{at} letter", letter),
            "margins": _margins(p.get("margins", {}), f"{at} margins", margins),
        }
    default = pack.get("default_preset")
    if default not in out:
        raise StylePackError(f"{where}: [pack] default_preset must name a preset ({', '.join(out)}), got {default!r}")
    notes = pack.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise StylePackError(f"{where}: [pack] notes must be a list of strings")
    snippet = typ.get("snippet", "")
    if not isinstance(snippet, str):
        raise StylePackError(f"{where}: [typst] snippet must be a string")
    return StylePack(
        name=pack["name"], root=root, presets=out, default_preset=default, fonts=tuple(family),
        font_paths=tuple(font_paths), font_size_pt=size_pt, letter=letter, margins=margins, text_pt=(lo, hi),
        size_tol_mm=_num(lint.get("size_tol_mm", 0.1), f"{where} [lint] size_tol_mm"),
        rules=tuple(_rule(r, i, where) for i, r in enumerate(rules)), typst=snippet, notes=tuple(notes))


@lru_cache(maxsize=16)
def _load(folder: Path, mtime: float) -> StylePack:
    f = folder / FILE
    try:
        doc = tomllib.loads(f.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise StylePackError(f"{f}: invalid TOML: {e}") from e
    return parse(doc, folder, str(f))


def load(path: Path | str) -> StylePack:
    """Loads a pack from its folder or its ``stylepack.toml``; cached until the file changes.

    Raises:
        StylePackError: The file is missing or invalid.
    """
    folder = Path(path).resolve()
    if folder.name == FILE:
        folder = folder.parent
    f = folder / FILE
    if not f.is_file():
        raise StylePackError(f"no {FILE} in {folder}")
    return _load(folder, f.stat().st_mtime)


def default() -> StylePack:
    """pintu's built-in neutral pack."""
    return load(BUILTIN / DEFAULT)


def resolve(spec: Optional[str], root: Path | str) -> StylePack:
    """A pack by built-in name, or by a path relative to ``root``; None gives the default."""
    if not spec:
        return default()
    if (BUILTIN / spec / FILE).is_file():
        return load(BUILTIN / spec)
    p = Path(root) / spec
    if not p.exists():
        raise StylePackError(f"style pack {spec!r} not found (not a built-in pack, and no {p})")
    return load(p)


def for_project(project) -> StylePack:
    """The pack ``[style] pack`` in a project's pintu.toml selects."""
    return resolve((project.settings.get("style") or {}).get("pack"), project.root)


def get(name: Optional[str], pack: Optional[StylePack] = None) -> dict:
    """The preset for a board's style name, from ``pack`` (default: the built-in pack).

    Keys: ``name``, ``widths``, ``max_height``, ``font``, ``font_size_pt``,
    ``letter`` (``size_pt``, ``weight``, ``case``, ``band_mm``, ``lower``,
    ``upper``) and ``margins``.
    """
    return (pack or default()).preset(name)


def margins(preset: dict) -> dict:
    """A preset's panel margins in mm (floats).

    Keys: outer ``left``, ``right``, ``top``, ``bottom``; ``gap`` between axes;
    ``tick``, added to a gap where inner tick labels show; ``title``, above each
    row of axes; ``key``, above the grid when there is a shared key.
    """
    return dict(preset["margins"])


@lru_cache(maxsize=8)
def typst_fonts(font_paths: tuple = ()):
    """A ``typst.Fonts`` of the system and embedded fonts plus the given folders."""
    import typst
    return typst.Fonts(font_paths=[str(p) for p in font_paths])


@lru_cache(maxsize=8)
def typst_families(font_paths: tuple = ()) -> frozenset:
    """Font family names Typst can find, lower-cased."""
    return frozenset(f.lower() for f in typst_fonts(font_paths).families())


def font_problems(pack: StylePack, mpl_found: Optional[list] = None) -> list[str]:
    """Missing-font warnings: the pack's first font not found by Typst, or by matplotlib.

    Args:
        pack: The pack.
        mpl_found: Pack families matplotlib found in the recipe kernel, if known.
    """
    out, first = [], pack.fonts[0]
    if first.lower() not in typst_families(pack.font_paths):
        out.append(f"font: {first!r} (style pack {pack.name!r}) is not found by Typst; board text falls back "
                   "to another font. Install it or add its folder to the pack's [fonts] paths.")
    if mpl_found is not None and first not in mpl_found:
        out.append(f"font: {first!r} (style pack {pack.name!r}) is not found by matplotlib in the recipe env; "
                   "recipes that ask for it fall back to another font.")
    return out


def rules_text(pack: StylePack, preset_name: Optional[str]) -> str:
    """The pack's rules as Markdown, for the LLM (SPEC §9)."""
    p = pack.preset(preset_name)
    widths = ", ".join(f"{v:g} mm ({k})" for k, v in p["widths"].items())
    lo, hi = pack.text_pt
    lines = [
        f"## Style rules (pack {pack.name!r}, preset {p['name']!r})",
        "",
        f"- Figure widths: {widths}; height at most {p['max_height']:g} mm.",
        "- The recipe draws one panel at exactly the cell size it gets (w, h in mm); the board places it.",
        f"- All text {lo:g}-{hi:g} pt; font {', '.join(pack.fonts[:2])} (sans-serif fallback).",
    ]
    for r in pack.rules:
        lines.append(f"- {PROPERTIES[r.property].split(',')[0].capitalize()}: {r.allowed()}"
                     + (f" ({r.message})" if r.message else "") + ".")
    lines += [
        "- The board draws the panel letter in a letter band above the figure; the recipe needs",
        "  no room for it.",
        "- No text may fall outside the figure.",
    ]
    lines += [f"- {n}" for n in pack.notes]
    return "\n".join(lines) + "\n"
