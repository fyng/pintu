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
    schema = 1                       # schema version; absent means 1; newer is refused
    source = {url = "...", ref = "..."}  # optional: the guide and commit/tag the pack follows
    notes = ["..."]                  # extra rules for the LLM, one line each; a warning
                                     # above NOTES_WARN_BYTES, an error above NOTES_MAX_BYTES

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
    color = "#000000"                # optional hex colour
    font = ["Arial"]                 # optional; default the board font

    [margins]                        # mm; panel margins for helpers such as multiples
    left = 12                        # also right, top, bottom, gap, tick, title, key

    [margins.scale]                  # optional: margins grow with the panel (``margins``)
    ref_mm = [30, 24]                # reference cell, where the margins are as given
    exponent = 0.5                   # s = (w*h / (ref_w*ref_h)) ** exponent
    discount = 0.5                   # k = 1 + discount * (s - 1)
    fixed = {left = 6}               # mm of a margin that does not scale (default 0)

    [presets.nature]
    widths = {col1 = 89, full = 183} # mm
    max_height = 170                 # mm
    letter = {case = "upper"}        # optional overrides of [letter]
    margins = {left = 12}            # optional overrides of [margins] (and its scale)
    fonts = {family = ["Arial"]}     # optional overrides of [fonts], per key
    lint = {text_pt = [5, 7]}        # optional overrides of [lint], per key; rules merge by id
    matplotlib = {rc = {"font.size" = 6}}  # optional overrides of [matplotlib] rc, per key

    [lint]
    text_pt = [5, 7]                 # allowed size of every drawn text
    size_tol_mm = 0.1                # render size tolerance

    [[lint.rules]]                   # extra rules: bounds on a property of the render
    id = "tick-labels"               # optional; defaults to the property
    property = "tick_label_pt"       # a key of PROPERTIES
    min = 5                          # min and/or max
    message = "ticks are 5 pt"       # optional hint shown with a violation

    [matplotlib]
    rc = {"axes.linewidth" = 0.5}    # rcParams the recipe kernel applies before each
                                     # render; unknown keys fail the render

    [typst]
    snippet = "#set text(...)"       # inserted after the board's page setup
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace
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
KEYS = {
    "pack": {"name", "default_preset", "notes", "schema", "source"},
    "pack.source": {"url", "ref"},
    "fonts": {"family", "paths", "size_pt"},
    "letter": {"size_pt", "weight", "case", "band_mm", "color", "font"},
    "margins": {*MARGIN_KEYS, "scale"},
    "margins.scale": {"ref_mm", "exponent", "discount", "fixed"},
    "lint": {"text_pt", "size_tol_mm", "rules"},
    "lint.rules": {"id", "property", "min", "max", "message"},
    "matplotlib": {"rc"},
    "typst": {"snippet"},
    "presets.*": {"widths", "max_height", "letter", "margins", "fonts", "lint", "matplotlib"},
}
"""Allowed keys of each table of the schema (see the module docstring)."""
_TOP = {"pack", "fonts", "letter", "margins", "presets", "lint", "matplotlib", "typst"}
_HEX = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SCHEMA = 1
"""Newest ``[pack] schema`` this pintu reads."""
NOTES_WARN_BYTES = 4096
"""``[pack] notes`` size (UTF-8, one line each) above which ``check`` warns."""
NOTES_MAX_BYTES = 16384
"""``[pack] notes`` size above which a pack is invalid."""


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
    schema: int = 1
    source: dict = field(default_factory=dict)
    rc: dict = field(default_factory=dict)
    views: dict = field(default_factory=dict, compare=False, repr=False)

    def preset(self, name: Optional[str]) -> dict:
        """The preset dict for a board's ``page.style``; unknown names give the default preset."""
        key = name if name in self.presets else self.default_preset
        return self.presets[key]

    def view(self, name: Optional[str]) -> "StylePack":
        """The pack with a preset's ``fonts``, ``lint`` and ``rc`` overrides merged in, for lint and rules."""
        return self.views.get(self.preset(name)["name"], self)

    def all_font_paths(self) -> tuple:
        """Font folders of the pack and all its presets, for one Typst compiler."""
        return tuple(dict.fromkeys(p for v in (self, *self.views.values()) for p in v.font_paths))


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
    if "color" in t:
        if not isinstance(t["color"], str) or not _HEX.match(t["color"]):
            raise StylePackError(f"{where} color must be a hex colour such as \"#1a1a1a\", got {t['color']!r}")
        out["color"] = t["color"]
    if "font" in t:
        f = [t["font"]] if isinstance(t["font"], str) else t["font"]
        if not isinstance(f, list) or not f or not all(isinstance(x, str) and x for x in f):
            raise StylePackError(f"{where} font must be a font name or a non-empty list of names")
        out["font"] = tuple(f)
    out["lower"] = out["case"] == "lower"
    out["upper"] = out["case"] == "upper"
    return out


def _margins(t: dict, where: str, base: dict, scale: Optional[dict]) -> tuple[dict, Optional[dict]]:
    """Margins and the scaling rule (None: fixed), merged per key over ``base`` and ``scale``."""
    out = dict(base)
    for k in t:
        if k == "scale":
            continue
        if k not in MARGIN_KEYS:
            raise StylePackError(f"{where}: unknown margin {k!r} (use {', '.join(MARGIN_KEYS)} or scale)")
        out[k] = _num(t[k], f"{where} {k}")
    if "scale" in t:
        s = t["scale"]
        if not isinstance(s, dict):
            raise StylePackError(f"{where} scale must be a table")
        extra = set(s) - KEYS["margins.scale"]
        if extra:
            raise StylePackError(f"{where} scale: unknown key(s): {', '.join(sorted(extra))}")
        scale = dict(scale or {"ref_mm": None, "exponent": 0.5, "discount": 1.0, "fixed": {}})
        if "ref_mm" in s:
            r = s["ref_mm"]
            if not isinstance(r, list) or len(r) != 2 or _num(r[0], f"{where} scale ref_mm") <= 0 \
                    or _num(r[1], f"{where} scale ref_mm") <= 0:
                raise StylePackError(f"{where} scale ref_mm must be [w, h] in mm, both > 0")
            scale["ref_mm"] = (float(r[0]), float(r[1]))
        if "exponent" in s:
            scale["exponent"] = _num(s["exponent"], f"{where} scale exponent")
        if "discount" in s:
            scale["discount"] = _num(s["discount"], f"{where} scale discount")
            if scale["discount"] > 1:
                raise StylePackError(f"{where} scale discount must be 0-1, got {s['discount']!r}")
        if "fixed" in s:
            if not isinstance(s["fixed"], dict):
                raise StylePackError(f"{where} scale fixed must be a table of margin = mm")
            fixed = dict(scale["fixed"])
            for k, v in s["fixed"].items():
                if k not in MARGIN_KEYS:
                    raise StylePackError(f"{where} scale fixed: unknown margin {k!r}")
                fixed[k] = _num(v, f"{where} scale fixed.{k}")
            scale["fixed"] = fixed
        if scale["ref_mm"] is None:
            raise StylePackError(f"{where} scale: ref_mm is required")
    if scale:
        for k, v in scale["fixed"].items():
            if v > out[k]:
                raise StylePackError(f"{where} scale fixed.{k} {v:g} is above the margin {out[k]:g}")
    return out, scale


def _rule(r, i: int, where: str) -> Rule:
    at = f"{where} [[lint.rules]] #{i + 1}"
    if not isinstance(r, dict):
        raise StylePackError(f"{at} must be a table")
    extra = set(r) - KEYS["lint.rules"]
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


def _fonts(t: dict, where: str, base: dict, root: Optional[Path]) -> dict:
    out = dict(base)
    if "family" in t:
        family = t["family"]
        if not isinstance(family, list) or not family or not all(isinstance(f, str) and f for f in family):
            raise StylePackError(f"{where}: [fonts] family must be a non-empty list of names")
        out["family"] = tuple(family)
    if "paths" in t:
        paths = t["paths"]
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StylePackError(f"{where}: [fonts] paths must be a list of folders")
        font_paths = []
        for p in paths:
            fp = (root / p) if root else Path(p)
            if not fp.is_dir():
                raise StylePackError(f"{where}: font folder not found: {p}")
            font_paths.append(fp.resolve())
        out["paths"] = tuple(font_paths)
    if "size_pt" in t:
        out["size_pt"] = _num(t["size_pt"], f"{where} [fonts] size_pt")
    return out


def _lint(t: dict, where: str, base: dict) -> dict:
    """Lint settings merged per key over ``base``; rules merge by id."""
    out = dict(base)
    if "text_pt" in t:
        text_pt = t["text_pt"]
        if not isinstance(text_pt, list) or len(text_pt) != 2:
            raise StylePackError(f"{where}: [lint] text_pt must be [min, max]")
        lo, hi = (_num(v, f"{where} [lint] text_pt") for v in text_pt)
        if lo > hi:
            raise StylePackError(f"{where}: [lint] text_pt min {lo:g} is above max {hi:g}")
        out["text_pt"] = (lo, hi)
    if "size_tol_mm" in t:
        out["size_tol_mm"] = _num(t["size_tol_mm"], f"{where} [lint] size_tol_mm")
    if "rules" in t:
        if not isinstance(t["rules"], list):
            raise StylePackError(f"{where}: [lint] rules must be an array of tables ([[lint.rules]])")
        rules = {r.id: r for r in out["rules"]}
        rules.update((r.id, r) for r in (_rule(r, i, where) for i, r in enumerate(t["rules"])))
        out["rules"] = tuple(rules.values())
    return out


def _rc(t: dict, where: str, base: dict) -> dict:
    """A ``[matplotlib]`` table's ``rc``, merged per key over ``base``."""
    rc = t.get("rc", {})
    if not isinstance(rc, dict):
        raise StylePackError(f"{where}: rc must be a table of rcParams")
    scalar = lambda v: isinstance(v, (str, int, float, bool))  # noqa: E731
    for k, v in rc.items():
        if not (scalar(v) or isinstance(v, list) and all(scalar(x) for x in v)):
            raise StylePackError(f"{where}: rc {k!r} must be a string, number, boolean or array of them "
                                 f"(quote dotted keys: \"{k}.…\" = …)")
    return {**base, **rc}


def parse(doc: dict, root: Optional[Path] = None, where: str = FILE) -> StylePack:
    """Validates a parsed ``stylepack.toml``.

    Args:
        doc: The parsed TOML.
        root: The pack folder; font paths resolve against it.
        where: Name used in error messages.

    Raises:
        StylePackError: The document does not match the schema.
    """
    pack = doc.get("pack", {})
    schema = pack.get("schema", 1) if isinstance(pack, dict) else 1
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise StylePackError(f"{where}: [pack] schema must be an integer >= 1, got {schema!r}")
    if schema > SCHEMA:
        raise StylePackError(f"{where}: pack schema {schema} is newer than this pintu supports ({SCHEMA}); "
                             "upgrade pintu to use this pack")
    extra = set(doc) - _TOP
    if extra:
        raise StylePackError(f"{where}: unknown table(s): {', '.join(sorted(extra))}")
    pack = _table(doc, "pack", where, KEYS["pack"])
    typ = _table(doc, "typst", where, KEYS["typst"])
    if not isinstance(pack.get("name"), str) or not pack["name"]:
        raise StylePackError(f"{where}: [pack] name is required")
    source = pack.get("source", {})
    if not isinstance(source, dict) or set(source) - KEYS["pack.source"] \
            or not all(isinstance(v, str) for v in source.values()):
        raise StylePackError(f"{where}: [pack] source must be a table {{url = \"...\", ref = \"...\"}} of strings")
    fonts = _fonts(_table(doc, "fonts", where, KEYS["fonts"]), where,
                   {"family": ("DejaVu Sans",), "paths": (), "size_pt": 7.0}, root)
    letter = _letter(_table(doc, "letter", where, KEYS["letter"]), f"{where} [letter]",
                     {"size_pt": 8.0, "weight": 700, "case": "lower", "band_mm": 3.5, "color": None, "font": None})
    margins, scale = _margins(_table(doc, "margins", where, KEYS["margins"]), f"{where} [margins]",
                              {"left": 12.0, "right": 1.5, "top": 1.0, "bottom": 9.0, "gap": 2.0, "tick": 4.0,
                               "title": 3.0, "key": 3.5}, None)
    lint = _lint(_table(doc, "lint", where, KEYS["lint"]), where,
                 {"text_pt": (5.0, 7.0), "size_tol_mm": 0.1, "rules": ()})
    rc = _rc(_table(doc, "matplotlib", where, KEYS["matplotlib"]), f"{where} [matplotlib]", {})
    presets = doc.get("presets")
    if not isinstance(presets, dict) or not presets:
        raise StylePackError(f"{where}: at least one [presets.<name>] table is required")
    out, merged = {}, {}
    for name, p in presets.items():
        at = f"{where} [presets.{name}]"
        if not isinstance(p, dict):
            raise StylePackError(f"{at} must be a table")
        bad = set(p) - KEYS["presets.*"]
        if bad:
            raise StylePackError(f"{at}: unknown key(s): {', '.join(sorted(bad))}")
        widths = p.get("widths")
        if not isinstance(widths, dict) or not widths:
            raise StylePackError(f"{at}: widths must be a table of name = mm")
        if "max_height" not in p:
            raise StylePackError(f"{at}: max_height is required")
        pf = _fonts(_table(p, "fonts", at, KEYS["fonts"]), at, fonts, root)
        pm, ps = _margins(p.get("margins", {}), f"{at} margins", margins, scale)
        merged[name] = (pf, _lint(_table(p, "lint", at, KEYS["lint"]), at, lint))
        out[name] = {
            "name": name,
            "widths": {k: _num(v, f"{at} widths.{k}") for k, v in widths.items()},
            "max_height": _num(p["max_height"], f"{at} max_height"),
            "font": list(pf["family"]),
            "font_size_pt": pf["size_pt"],
            "letter": _letter(p.get("letter", {}), f"{at} letter", letter),
            "margins": pm,
            "margin_scale": ps,
            "rc": _rc(_table(p, "matplotlib", at, KEYS["matplotlib"]), f"{at} matplotlib", rc),
        }
    default = pack.get("default_preset")
    if default not in out:
        raise StylePackError(f"{where}: [pack] default_preset must name a preset ({', '.join(out)}), got {default!r}")
    notes = pack.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise StylePackError(f"{where}: [pack] notes must be a list of strings")
    size = notes_bytes(notes)
    if size > NOTES_MAX_BYTES:
        raise StylePackError(f"{where}: [pack] notes are {size} bytes; the limit is {NOTES_MAX_BYTES}")
    snippet = typ.get("snippet", "")
    if not isinstance(snippet, str):
        raise StylePackError(f"{where}: [typst] snippet must be a string")
    base = StylePack(
        name=pack["name"], root=root, presets=out, default_preset=default, fonts=fonts["family"],
        font_paths=fonts["paths"], font_size_pt=fonts["size_pt"], letter=letter, margins=margins,
        text_pt=lint["text_pt"], size_tol_mm=lint["size_tol_mm"], rules=lint["rules"], typst=snippet,
        notes=tuple(notes), schema=schema, source=dict(source), rc=rc)
    for name, (pf, pl) in merged.items():
        base.views[name] = replace(base, fonts=pf["family"], font_paths=pf["paths"], font_size_pt=pf["size_pt"],
                                   text_pt=pl["text_pt"], size_tol_mm=pl["size_tol_mm"], rules=pl["rules"],
                                   rc=out[name]["rc"])
    return base


def notes_bytes(notes) -> int:
    """UTF-8 size of a pack's notes, one line each."""
    return sum(len(n.encode("utf-8")) + 1 for n in notes)


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
    ``letter`` (``size_pt``, ``weight``, ``case``, ``band_mm``, ``color``,
    ``font``, ``lower``, ``upper``), ``margins``, ``margin_scale`` (the
    ``[margins.scale]`` rule or None) and ``rc`` (merged matplotlib rcParams).
    """
    return (pack or default()).preset(name)


def margins(preset: dict, w: Optional[float] = None, h: Optional[float] = None) -> dict:
    """A preset's panel margins in mm (floats), scaled to a w x h mm panel if the pack has a rule.

    Keys: outer ``left``, ``right``, ``top``, ``bottom``; ``gap`` between axes;
    ``tick``, added to a gap where inner tick labels show; ``title``, above each
    row of axes; ``key``, above the grid when there is a shared key.

    With ``[margins.scale]`` and w, h given, each margin m with fixed part f
    becomes ``f + (m - f) * k``, ``k = 1 + discount * (s - 1)``,
    ``s = (w * h / (ref_w * ref_h)) ** exponent``. Otherwise the margins are as given.
    """
    out = dict(preset["margins"])
    sc = preset.get("margin_scale")
    if sc and w is not None and h is not None and w > 0 and h > 0:
        rw, rh = sc["ref_mm"]
        k = 1 + sc["discount"] * ((w * h / (rw * rh)) ** sc["exponent"] - 1)
        for key, v in out.items():
            f = sc["fixed"].get(key, 0.0)
            out[key] = round(f + (v - f) * k, 3)
    return out


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
    """The pack's rules as Markdown, for the LLM (SPEC §9), with the preset's overrides."""
    pack = pack.view(preset_name)
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
    if pack.rc:
        lines.append("- pintu applies the pack's matplotlib rcParams before each render; recipes need not set them.")
    lines += [f"- {n}" for n in pack.notes]
    return "\n".join(lines) + "\n"


def warnings(pack: StylePack) -> list[str]:
    """Problems that do not make a pack invalid: large notes, fonts Typst cannot find."""
    out = []
    size = notes_bytes(pack.notes)
    if size > NOTES_WARN_BYTES:
        out.append(f"notes: {size} bytes, above the {NOTES_WARN_BYTES}-byte budget; every agent prompt carries them")
    base = font_problems(pack)
    out += base
    for name in pack.presets:
        out += [f"preset {name!r}: {w}" for w in font_problems(pack.view(name)) if w not in base]
    return out


def check(path: Path | str, out=print) -> int:
    """``pintu stylepack check``: validates a pack, prints its presets, warnings and rules text.

    Returns:
        Exit status: 0 if the pack is valid, 1 if not.
    """
    try:
        pack = load(path)
    except StylePackError as e:
        out(f"error: {e}")
        return 1
    src = " @ ".join(pack.source[k] for k in ("url", "ref") if k in pack.source)
    out(f"pack {pack.name!r} (schema {pack.schema}{', source ' + src if src else ''}): ok")
    for name, p in pack.presets.items():
        widths = ", ".join(f"{k} {v:g}" for k, v in p["widths"].items())
        out(f"  preset {name}{' (default)' if name == pack.default_preset else ''}: {widths} mm; "
            f"max height {p['max_height']:g} mm; font {p['font'][0]}")
    for w in warnings(pack):
        out(f"warning: {w}")
    for name in pack.presets:
        out("")
        out(rules_text(pack, name).rstrip("\n"))
    return 0
