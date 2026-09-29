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
from . import multiples as mult
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
    """A board: page settings, panels and groups.

    A group (``groups: [{id, panels: [...], letter?}]``) is several panels that
    share one letter: the group is one lettered unit in reading order, placed by
    the bounding box of its members' cells, and its members take no letter of
    their own.

    Attributes:
        doc: The round-trip YAML document.
        pack: The project's style pack; None means pintu's default pack.
    """

    def __init__(self, doc: CommentedMap, pack: Optional[styles.StylePack] = None):
        self.doc = doc
        self.pack = pack
        self.validate()

    @classmethod
    def new(cls, width: float = 183, height: float = 170, grid: int = 36, gutter: float = 3,
            pack: Optional[styles.StylePack] = None) -> "Board":
        """An empty board on the pack's default preset (Nature full width in the default pack)."""
        doc = CommentedMap()
        doc["version"] = VERSION
        doc["page"] = _flow_map({"width": width, "height": height, "grid": _flow([grid, grid]),
                                 "gutter": gutter, "style": (pack or styles.default()).default_preset})
        doc["panels"] = CommentedSeq()
        return cls(doc, pack)

    @classmethod
    def loads(cls, text: str, pack: Optional[styles.StylePack] = None) -> "Board":
        """Parses board YAML."""
        try:
            doc = _yaml().load(text)
        except Exception as e:  # ruamel raises several error types
            raise BoardError(f"invalid YAML: {e}") from e
        if not isinstance(doc, CommentedMap):
            raise BoardError("board must be a mapping")
        return cls(doc, pack)

    @classmethod
    def load(cls, path: Path, pack: Optional[styles.StylePack] = None) -> "Board":
        """Reads a board file."""
        return cls.loads(Path(path).read_text(encoding="utf-8"), pack)

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
    def preset(self) -> dict:
        """The style preset ``page.style`` picks in the board's pack (``style.get``)."""
        return styles.get(self.style, self.pack)

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

    @property
    def groups(self) -> list[CommentedMap]:
        """The group mappings, in file order (empty if the board has none)."""
        return self.doc.get("groups") or []

    def group(self, gid: str) -> CommentedMap:
        """The group with the given id."""
        for g in self.groups:
            if g["id"] == gid:
                return g
        raise BoardError(f"no group {gid!r}")

    def group_of(self, pid: str) -> Optional[CommentedMap]:
        """The group a panel belongs to, or None."""
        return next((g for g in self.groups if pid in g["panels"]), None)

    def group_cell(self, g: dict) -> geo.Cell:
        """Bounding box of a group's member cells, in grid lines."""
        cs = [self.panel(pid)["cell"] for pid in g["panels"]]
        return (min(c[0] for c in cs), min(c[1] for c in cs), max(c[2] for c in cs), max(c[3] for c in cs))

    def group_anchor(self, g: dict) -> str:
        """The member that draws the group's letter: the leftmost of those on the group's top edge."""
        top = self.group_cell(g)[1]
        return min((pid for pid in g["panels"] if self.panel(pid)["cell"][1] == top),
                   key=lambda pid: self.panel(pid)["cell"][0])

    def unit_letters(self) -> dict[str, Optional[str]]:
        """Letters of the lettered units, ungrouped panels and groups, by id."""
        cells: dict[str, geo.Cell] = {}
        settings: dict[str, object] = {}
        grouped = {pid for g in self.groups for pid in g["panels"]}
        units = [(p["id"], tuple(p["cell"]), p) for p in self.panels if p["id"] not in grouped]
        units += [(g["id"], self.group_cell(g), g) for g in self.groups]
        for uid, cell, m in units:
            cells[uid] = cell
            if "letter" in m:
                v = m["letter"]
                settings[uid] = None if v is None or v is False else str(v)
        return geo.assign_letters(cells, settings)

    def letters(self) -> dict[str, Optional[str]]:
        """Panel id to the letter drawn on it; a group's letter is drawn on its anchor member."""
        units = self.unit_letters()
        out = {p["id"]: units.get(p["id"]) for p in self.panels}
        for g in self.groups:
            for pid in g["panels"]:
                out[pid] = None
            out[self.group_anchor(g)] = units[g["id"]]
        return out

    @property
    def letter_band(self) -> float:
        """Height in mm of the letter band: ``page.letter_band`` or the style's ``band_mm``."""
        v = (self.doc.get("page") or {}).get("letter_band")
        return float(v) if v is not None else float(self.preset["letter"]["band_mm"])

    def bands(self) -> dict[str, float]:
        """Panel id to the letter band at its top: the board's band if lettered, else 0.

        A lettered group's band lies along the top edge of its bounding box: the
        members on that edge reserve it, members below it do not. The band is
        capped at half the cell height.
        """
        page, band, units = self.page, self.letter_band, self.unit_letters()
        on = {p["id"]: bool(units.get(p["id"])) for p in self.panels}
        for g in self.groups:
            top = self.group_cell(g)[1]
            for pid in g["panels"]:
                on[pid] = bool(units[g["id"]]) and self.panel(pid)["cell"][1] == top
        return {p["id"]: min(band, page.rect(p["cell"])[3] / 2) if on[p["id"]] else 0.0 for p in self.panels}

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
            if isinstance(src, dict) and "multiples" in src:
                try:
                    mult.validate(src["multiples"])
                except mult.MosaicError as e:
                    raise BoardError(f"panel {pid!r}: {e}") from e
        groups = self.doc.get("groups")
        if groups is not None and not isinstance(groups, list):
            raise BoardError("groups must be a list")
        panel_ids, member = set(seen), set()
        for g in groups or []:
            if not isinstance(g, dict) or not isinstance(g.get("id"), str) or not ID_RE.match(g["id"]):
                raise BoardError("each group needs a valid id")
            if g["id"] in seen:
                raise BoardError(f"duplicate id {g['id']!r}: group ids must differ from panel and group ids")
            seen.add(g["id"])
            ps = g.get("panels")
            if not isinstance(ps, list) or len(ps) < 2:
                raise BoardError(f"group {g['id']!r}: panels must list at least 2 panel ids")
            for pid in ps:
                if pid not in panel_ids:
                    raise BoardError(f"group {g['id']!r}: no panel {pid!r}")
                if pid in member:
                    raise BoardError(f"panel {pid!r} is in more than one group")
                member.add(pid)
        pairs = geo.find_overlaps(self.cells())
        if pairs:
            raise BoardError("overlapping panels: " + ", ".join(f"{a}/{b}" for a, b in pairs))

    # -- editing ---------------------------------------------------------

    def _fresh_id(self, base: str) -> str:
        ids = {p["id"] for p in self.panels} | {g["id"] for g in self.groups}
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
        """Deletes a panel; a group left with one member is dissolved."""
        self.panels.remove(self.panel(pid))
        self._leave_group(pid)

    def _leave_group(self, pid: str) -> None:
        g = self.group_of(pid)
        if g is not None:
            g["panels"].remove(pid)
            if len(g["panels"]) < 2:
                self.ungroup(g["id"])

    def add_group(self, ids: list[str], gid: Optional[str] = None) -> str:
        """Groups panels under one letter; they leave any group they were in. Returns the group id."""
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            raise BoardError("a group needs at least 2 panels")
        for pid in ids:
            self.panel(pid)
            self._leave_group(pid)
        gid = self._fresh_id(gid or "group")
        if "groups" not in self.doc:
            self.doc["groups"] = CommentedSeq()
        self.doc["groups"].append(_flow_map({"id": gid, "panels": _flow(ids)}))
        return gid

    def ungroup(self, gid: str) -> None:
        """Dissolves a group; its members take letters of their own again."""
        self.doc["groups"].remove(self.group(gid))
        if not self.doc["groups"]:
            del self.doc["groups"]

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

    def set_multiples(self, pid: str, **fields: Any) -> None:
        """Makes a recipe panel a multiples panel, or edits its ``source.multiples``.

        Args:
            pid: Panel id.
            **fields: ``item``, ``mosaic``, ``share``, ``width_ratios``,
                ``height_ratios``; None removes an optional key. A new multiples
                block without a mosaic starts from the item's param value as a
                1 x 1 mosaic; the item param leaves ``params``. A mosaic of a new
                shape drops the ratios not given.
        """
        bad = set(fields) - {"item", "mosaic", "share", "width_ratios", "height_ratios"}
        if bad:
            raise BoardError(f"unknown multiples keys: {', '.join(sorted(bad))}")
        src = self.panel(pid).get("source")
        if not isinstance(src, dict) or "recipe" not in src:
            raise BoardError(f"panel {pid!r}: multiples need a recipe source")
        m = src.get("multiples")
        if not isinstance(m, dict):
            item = fields.get("item")
            if not item:
                raise BoardError("a new multiples panel needs an item")
            params = src.get("params")
            if "mosaic" not in fields:
                if not params or item not in params:
                    raise BoardError(f"panel {pid!r}: no {item!r} param to start the mosaic from")
                fields["mosaic"] = [[params[item]]]
            if params and item in params:
                del params[item]
                if not params:
                    del src["params"]
            m = src["multiples"] = _flow_map({"item": str(item)})
        if "mosaic" in fields:
            try:
                new = mult.parse(fields["mosaic"])[:2]
                old = mult.parse(m["mosaic"])[:2] if "mosaic" in m else None
            except mult.MosaicError as e:
                raise BoardError(str(e)) from e
            if old != new:
                for k in ("width_ratios", "height_ratios"):
                    if k not in fields:
                        m.pop(k, None)
            fields["mosaic"] = _flow([_flow([str(v) for v in row]) for row in fields["mosaic"]])
        if isinstance(fields.get("share"), dict):
            fields["share"] = _flow_map(dict(fields["share"]))
        for k in ("width_ratios", "height_ratios"):
            if fields.get(k) is not None:
                fields[k] = _flow(list(fields[k]))
        for k, v in fields.items():
            if v is not None:
                m[k] = v
            elif k not in ("item", "mosaic"):
                m.pop(k, None)
        try:
            mult.validate(m)
        except mult.MosaicError as e:
            raise BoardError(f"panel {pid!r}: {e}") from e

    def set_letter(self, pid: str, letter: Any) -> None:
        """Sets a unit's letter: a string, None for no letter, "auto" to clear the override.

        ``pid`` is a panel or group id; a grouped panel sets its group's letter.
        """
        p = self.group(pid) if any(g["id"] == pid for g in self.groups) else self.group_of(pid) or self.panel(pid)
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
