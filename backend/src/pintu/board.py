"""Board model backed by a round-trip YAML document (SPEC §7).

The board keeps the parsed ruamel document, so edits leave key order, comments
and keys pintu does not know untouched.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import geometry as geo
from . import style as styles

VERSION = 1
ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class BoardError(ValueError):
    """An invalid board or edit."""


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _flow(items: list) -> CommentedSeq:
    s = CommentedSeq(items)
    s.fa.set_flow_style()
    return s


def _flow_map(d: dict) -> CommentedMap:
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


class Board:
    """A board: page settings and panels.

    Attributes:
        doc: The round-trip YAML document.
    """

    def __init__(self, doc: CommentedMap):
        self.doc = doc
        self.validate()

    @classmethod
    def new(cls, width: float = 183, height: float = 170, grid: int = 36, gutter: float = 3) -> "Board":
        """An empty board with the Nature full-width page."""
        doc = CommentedMap()
        doc["version"] = VERSION
        doc["page"] = _flow_map({"width": width, "height": height, "grid": _flow([grid, grid]),
                                 "gutter": gutter, "style": "nature"})
        doc["panels"] = CommentedSeq()
        return cls(doc)

    @classmethod
    def loads(cls, text: str) -> "Board":
        """Parses board YAML."""
        try:
            doc = _yaml().load(text)
        except Exception as e:  # ruamel raises several error types
            raise BoardError(f"invalid YAML: {e}") from e
        if not isinstance(doc, CommentedMap):
            raise BoardError("board must be a mapping")
        return cls(doc)

    @classmethod
    def load(cls, path: Path) -> "Board":
        """Reads a board file."""
        return cls.loads(Path(path).read_text(encoding="utf-8"))

    def dumps(self) -> str:
        """Serialises the board to YAML."""
        buf = io.StringIO()
        _yaml().dump(self.doc, buf)
        return buf.getvalue()

    # -- reading ---------------------------------------------------------

    @property
    def page(self) -> geo.Page:
        """The page geometry."""
        p = self.doc.get("page") or {}
        grid = p.get("grid", 36)
        nx, ny = (grid, grid) if isinstance(grid, int) else (grid[0], grid[1])
        return geo.Page(width=float(p.get("width", 183)), height=float(p.get("height", 170)),
                        nx=int(nx), ny=int(ny), gutter=float(p.get("gutter", 3)))

    @property
    def style(self) -> str:
        """The page's style name."""
        return str((self.doc.get("page") or {}).get("style", "nature"))

    @property
    def panels(self) -> list[CommentedMap]:
        """The panel mappings, in file order."""
        return self.doc.setdefault("panels", CommentedSeq())

    def panel(self, pid: str) -> CommentedMap:
        """The panel with the given id."""
        for p in self.panels:
            if p["id"] == pid:
                return p
        raise BoardError(f"no panel {pid!r}")

    def cells(self) -> dict[str, geo.Cell]:
        """Panel id to cell."""
        return {p["id"]: tuple(p["cell"]) for p in self.panels}

    def letters(self) -> dict[str, Optional[str]]:
        """Resolved panel letters."""
        settings: dict[str, object] = {}
        for p in self.panels:
            if "letter" in p:
                v = p["letter"]
                settings[p["id"]] = None if v is None or v is False else str(v)
        return geo.assign_letters(self.cells(), settings)

    @property
    def letter_band(self) -> float:
        """Height in mm of the letter band: ``page.letter_band`` or the style's ``band_mm``."""
        v = (self.doc.get("page") or {}).get("letter_band")
        return float(v) if v is not None else float(styles.get(self.style)["letter"]["band_mm"])

    def bands(self) -> dict[str, float]:
        """Panel id to the letter band at its top: the board's band if lettered, else 0.

        The band is capped at half the cell height.
        """
        page, band, letters = self.page, self.letter_band, self.letters()
        return {p["id"]: min(band, page.rect(p["cell"])[3] / 2) if letters[p["id"]] else 0.0
                for p in self.panels}

    def content_rect(self, panel: dict, band: Optional[float] = None) -> tuple[float, float, float, float]:
        """(x, y, w, h) in mm of the area below a panel's letter band; a recipe renders at (w, h).

        Args:
            panel: The panel mapping.
            band: The panel's band, if already known; otherwise computed.
        """
        if band is None:
            band = self.bands()[panel["id"]]
        x, y, w, h = self.page.rect(panel["cell"])
        return x, y + band, w, h - band

    def validate(self) -> None:
        """Checks the schema, cell bounds and overlaps.

        Raises:
            BoardError: The board is invalid.
        """
        if self.doc.get("version", VERSION) != VERSION:
            raise BoardError(f"unsupported board version {self.doc.get('version')!r}")
        page = self.page
        if page.width <= 0 or page.height <= 0 or page.nx < 1 or page.ny < 1 or page.gutter < 0:
            raise BoardError("invalid page")
        band = (self.doc.get("page") or {}).get("letter_band")
        if band is not None and (isinstance(band, bool) or not isinstance(band, (int, float)) or band < 0):
            raise BoardError("page letter_band must be a number >= 0")
        seen = set()
        for p in self.panels:
            if not isinstance(p, dict) or "id" not in p or "cell" not in p:
                raise BoardError("each panel needs an id and a cell")
            pid = p["id"]
            if not isinstance(pid, str) or not ID_RE.match(pid):
                raise BoardError(f"invalid panel id {pid!r}")
            if pid in seen:
                raise BoardError(f"duplicate panel id {pid!r}")
            seen.add(pid)
            c = p["cell"]
            if not (isinstance(c, list) and len(c) == 4 and all(isinstance(v, int) for v in c)):
                raise BoardError(f"panel {pid!r}: cell must be [x0, y0, x1, y1] integers")
            if not geo.valid_cell(c, page):
                raise BoardError(f"panel {pid!r}: cell {list(c)} is outside the {page.nx}x{page.ny} grid")
            src = p.get("source")
            if src is not None and not isinstance(src, dict):
                raise BoardError(f"panel {pid!r}: source must be a mapping")
        pairs = geo.find_overlaps(self.cells())
        if pairs:
            raise BoardError("overlapping panels: " + ", ".join(f"{a}/{b}" for a, b in pairs))

    # -- editing ---------------------------------------------------------

    def _fresh_id(self, base: str) -> str:
        ids = {p["id"] for p in self.panels}
        base = re.sub(r"[^A-Za-z0-9_.-]", "-", base).strip("-.") or "panel"
        if not ID_RE.match(base):
            base = "p-" + base
        if base not in ids:
            return base
        i = 2
        while f"{base}-{i}" in ids:
            i += 1
        return f"{base}-{i}"

    def set_cell(self, pid: str, cell: list[int]) -> None:
        """Moves or resizes a panel."""
        seq = self.panel(pid)["cell"]
        seq[:] = [int(v) for v in cell]

    def add_panel(self, cell: list[int], file: Optional[str] = None, pid: Optional[str] = None) -> str:
        """Adds a panel, optionally with a static file source. Returns its id."""
        pid = self._fresh_id(pid or (Path(file).stem if file else "panel"))
        p = CommentedMap()
        p["id"] = pid
        p["cell"] = _flow([int(v) for v in cell])
        if file:
            p["source"] = _flow_map({"file": file})
        self.panels.append(p)
        return pid

    def remove_panel(self, pid: str) -> None:
        """Deletes a panel."""
        self.panels.remove(self.panel(pid))

    def set_source_file(self, pid: str, file: Optional[str]) -> None:
        """Sets a panel's static file source, or removes the source if None."""
        p = self.panel(pid)
        if file is None:
            p.pop("source", None)
        elif isinstance(p.get("source"), dict):
            src = p["source"]
            for k in ("recipe", "multiples"):
                src.pop(k, None)
            src["file"] = file
        else:
            p["source"] = _flow_map({"file": file})

    def set_recipe(self, pid: str, recipe: str, params: Optional[dict] = None) -> None:
        """Sets a panel's recipe source (``module:function``) and its params.

        Args:
            pid: Panel id.
            recipe: ``module.path:function``.
            params: Keyword arguments; None keeps the current ones, {} clears them.
        """
        mod, _, fn = recipe.partition(":")
        if not mod or not fn:
            raise BoardError(f"recipe must be 'module:function', got {recipe!r}")
        p = self.panel(pid)
        src = p.get("source")
        if not isinstance(src, dict):
            src = p["source"] = _flow_map({})
        src.pop("file", None)
        src["recipe"] = recipe
        if params == {}:
            src.pop("params", None)
        elif params is not None:
            if not isinstance(params, dict):
                raise BoardError("params must be a mapping")
            src["params"] = _flow_map(dict(params))

    def set_letter(self, pid: str, letter: Any) -> None:
        """Sets a panel's letter: a string, None for no letter, "auto" to clear the override."""
        p = self.panel(pid)
        if letter == "auto":
            p.pop("letter", None)
        elif letter is None or letter is False or letter == "":
            p["letter"] = False
        else:
            p["letter"] = str(letter)

    def split(self, pid: str, n: int, axis: str = "x") -> tuple[list[str], bool]:
        """Splits a panel into n parts; the first keeps the id and source.

        Returns:
            The ids of the parts, and whether the split is exact.
        """
        p = self.panel(pid)
        parts, exact = geo.split(tuple(p["cell"]), n, axis)
        self.set_cell(pid, list(parts[0]))
        ids = [pid]
        idx = self.panels.index(p)
        for k, c in enumerate(parts[1:], start=1):
            q = CommentedMap()
            q["id"] = self._fresh_id(pid)
            q["cell"] = _flow(list(c))
            self.panels.insert(idx + k, q)
            ids.append(q["id"])
        return ids, exact

    def set_page(self, **kw: Any) -> None:
        """Updates page keys (width, height, grid, gutter, style, letter_band)."""
        page = self.doc.setdefault("page", _flow_map({}))
        for k, v in kw.items():
            if k == "grid" and isinstance(v, (list, tuple)):
                v = _flow([int(x) for x in v])
            page[k] = v
