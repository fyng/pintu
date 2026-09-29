"""Gallery catalog: image and PDF files under the scan paths, in ``.pintu/catalog.sqlite`` (SPEC §7).

A file is *linked* when a sidecar names its recipe. The sidecar of ``x.pdf`` is
``x.pdf.meta.json`` beside it (written by ``pintu_sdk.save``), else ``meta.json``
in its folder, which covers every output there (pintu's own
``pintu_out/<recipe>/<param-hash>/`` folders). Linked outputs with the same
recipe and params are one gallery item: the newest file. Rescans are
incremental: a file is re-read only when its or its sidecar's mtime or size changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Optional

import typst

from . import codegen
from .project import IMAGE_KINDS, PathError, Project

DB = ".pintu/catalog.sqlite"
THUMBS = ".pintu/thumbs"
SIDECAR = "meta.json"
SIDECAR_SUFFIX = ".meta.json"
THUMB_WIDTH_PT = 120
THUMB_PPI = 144
SKIP_DIRS = {"node_modules", "__pycache__"}
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, kind TEXT, mtime_ns INTEGER, size INTEGER, meta_sig TEXT,
  recipe TEXT, params TEXT, width_mm REAL, height_mm REAL, created TEXT, link TEXT);
CREATE TABLE IF NOT EXISTS params (path TEXT, key TEXT, value TEXT);
CREATE INDEX IF NOT EXISTS params_kv ON params (key, value);
CREATE INDEX IF NOT EXISTS params_path ON params (path);
CREATE INDEX IF NOT EXISTS files_link ON files (link, mtime_ns);
"""


def param_value(v) -> str:
    """The text a param value is filtered by: strings as is, others as JSON."""
    return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def sidecar_for(path: Path) -> Optional[Path]:
    """A file's sidecar: ``<name>.meta.json``, else the folder's ``meta.json``."""
    own = path.with_name(path.name + SIDECAR_SUFFIX)
    if own.is_file():
        return own
    folder = path.with_name(SIDECAR)
    return folder if folder.is_file() else None


def read_sidecar(path: Path) -> Optional[dict]:
    """The sidecar fields for a file, or None if it has no valid sidecar.

    A folder-level sidecar's ``width_mm``/``height_mm`` describe its newest render;
    for other files in the folder the size comes from a ``<w>x<h>`` file name.
    """
    side = sidecar_for(path)
    if side is None:
        return None
    try:
        meta = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    meta = dict(meta)
    if not isinstance(meta.get("params"), dict):
        meta["params"] = {}
    m = _SIZE.match(path.stem)
    if side.name == SIDECAR and m:
        meta["width_mm"], meta["height_mm"] = float(m[1]), float(m[2])
    return meta


def _sig(p: Optional[Path]) -> str:
    if p is None:
        return ""
    st = p.stat()
    return f"{p.name}:{st.st_mtime_ns}:{st.st_size}"


class Catalog:
    """The gallery index of one project.

    Args:
        project: The project; scan paths come from ``[gallery] paths`` in
            pintu.toml (default: the whole project).
    """

    def __init__(self, project: Project):
        self.project = project
        self.path = project.root / DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript("PRAGMA journal_mode=WAL;" + SCHEMA)
        self._lock = threading.RLock()
        self._thumb_lock = threading.Lock()
        self._compiler: Optional[typst.Compiler] = None
        self.scanned = False

    def close(self) -> None:
        self._db.close()

    def scan_paths(self) -> list[Path]:
        """Scan folders, confined to the project root; they need not exist yet."""
        rels = (self.project.settings.get("gallery") or {}).get("paths") or ["."]
        out = []
        for rel in rels:
            try:
                p = self.project.resolve(str(rel))
            except PathError:
                continue
            out.append(p)
        return out

    def _walk(self) -> Iterable[Path]:
        seen = set()
        for base in self.scan_paths():
            for d, dirs, files in os.walk(base):
                dirs[:] = [x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS
                           and Path(d, x) != self.project.build_dir]
                for f in files:
                    p = Path(d, f)
                    if p.suffix.lower() in IMAGE_KINDS and not f.startswith(".") and p not in seen:
                        seen.add(p)
                        yield p

    def scan(self) -> int:
        """Brings the index up to date. Returns the number of files added, changed or removed."""
        with self._lock:
            known = {r[0]: (r[1], r[2], r[3]) for r in
                     self._db.execute("SELECT path, mtime_ns, size, meta_sig FROM files")}
            live, changed = set(), 0
            for p in self._walk():
                try:
                    rel = self.project.relative(p)
                    st = p.stat()
                    sig = _sig(sidecar_for(p))
                except (OSError, ValueError):
                    continue
                live.add(rel)
                if known.get(rel) == (st.st_mtime_ns, st.st_size, sig):
                    continue
                self._put(rel, p, st, sig)
                changed += 1
            gone = [k for k in known if k not in live]
            for rel in gone:
                self._db.execute("DELETE FROM files WHERE path = ?", (rel,))
                self._db.execute("DELETE FROM params WHERE path = ?", (rel,))
            self._db.commit()
            self.scanned = True
            return changed + len(gone)

    def _put(self, rel: str, p: Path, st: os.stat_result, sig: str) -> None:
        meta = read_sidecar(p)
        recipe = meta.get("recipe") if meta else None
        params = (meta or {}).get("params") or {}
        pjson = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
        link = f"{recipe}|{pjson}" if recipe else rel
        self._db.execute(
            "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rel, IMAGE_KINDS[p.suffix.lower()], st.st_mtime_ns, st.st_size, sig, recipe,
             pjson if recipe else None, (meta or {}).get("width_mm"), (meta or {}).get("height_mm"),
             (meta or {}).get("created"), link))
        self._db.execute("DELETE FROM params WHERE path = ?", (rel,))
        if recipe:
            self._db.executemany("INSERT INTO params VALUES (?,?,?)",
                                 [(rel, k, param_value(v)) for k, v in params.items()])

    def _where(self, recipe: Optional[str], q: str, filters: dict[str, list[str]]) -> tuple[str, list]:
        sql, args = ["1"], []
        if recipe == "":
            sql.append("recipe IS NULL")
        elif recipe is not None:
            sql.append("recipe = ?")
            args.append(recipe)
        if q:
            sql.append("(path LIKE ? OR recipe LIKE ? OR params LIKE ?)")
            args += [f"%{q}%"] * 3
        for key, values in filters.items():
            if values:
                sql.append(f"path IN (SELECT path FROM params WHERE key = ? AND value IN ({','.join('?' * len(values))}))")
                args += [key, *values]
        return " AND ".join(sql), args

    def _items_sql(self, where: str) -> str:
        return (f"SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY link ORDER BY mtime_ns DESC, path) AS rn "
                f"FROM files WHERE {where}) WHERE rn = 1")

    def query(self, recipe: Optional[str] = None, q: str = "", filters: Optional[dict[str, list[str]]] = None,
              offset: int = 0, limit: int = 60) -> dict:
        """One page of gallery items, grouped by recipe (unlinked files last).

        Args:
            recipe: Only this recipe's outputs; "" for unlinked files; None for all.
            q: Substring of the path, recipe or params.
            filters: Param key → accepted values; values of one key OR, keys AND.
            offset: Items to skip.
            limit: Page size.

        Returns:
            ``total``, ``offset``, ``items`` and ``groups`` (``recipe``, ``count``
            over all recipes, under ``q`` only).
        """
        filters = filters or {}
        with self._lock:
            where, args = self._where(recipe, q, filters)
            base = self._items_sql(where)
            total = self._db.execute(f"SELECT COUNT(*) FROM ({base})", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT path, kind, mtime_ns, recipe, params, width_mm, height_mm, created FROM ({base}) "
                "ORDER BY recipe IS NULL, recipe, path LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            gw, ga = self._where(None, q, {})
            groups = self._db.execute(
                f"SELECT recipe, COUNT(*) FROM ({self._items_sql(gw)}) GROUP BY recipe "
                "ORDER BY recipe IS NULL, recipe", ga).fetchall()
        items = [{"path": r[0], "kind": r[1], "version": r[2], "recipe": r[3],
                  "params": json.loads(r[4]) if r[4] else None, "width_mm": r[5], "height_mm": r[6],
                  "created": r[7]} for r in rows]
        return {"total": total, "offset": offset, "items": items,
                "groups": [{"recipe": g[0], "count": g[1]} for g in groups]}

    def facets(self, recipe: str, max_values: int = 1000) -> dict[str, list[dict]]:
        """A recipe's param keys, each with its values and item counts."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT key, value, COUNT(*) FROM params WHERE path IN "
                f"(SELECT path FROM ({self._items_sql('recipe = ?')})) GROUP BY key, value ORDER BY key, value",
                (recipe,)).fetchall()
        out: dict[str, list[dict]] = {}
        for k, v, n in rows:
            vals = out.setdefault(k, [])
            if len(vals) < max_values:
                vals.append({"value": v, "count": n})
        return out

    def thumb(self, rel: str) -> Path:
        """A PNG thumbnail of a catalogued file, cached under ``.pintu/thumbs``.

        Raises:
            KeyError: The file is not in the catalog.
        """
        with self._lock:
            row = self._db.execute("SELECT mtime_ns FROM files WHERE path = ?", (rel,)).fetchone()
        if row is None:
            raise KeyError(rel)
        stem = hashlib.sha1(rel.encode()).hexdigest()[:20]
        out = self.project.root / THUMBS / f"{stem}-{row[0]}.png"
        if out.is_file():
            return out
        doc = (f"#set page(width: {THUMB_WIDTH_PT}pt, height: auto, margin: 0pt)\n"
               f"#image({codegen.typst_str('/' + rel)}, width: 100%)\n")
        with self._thumb_lock:
            if self._compiler is None:
                self._compiler = typst.Compiler(root=str(self.project.root))
            png = self._compiler.compile(input=doc.encode(), format="png", ppi=THUMB_PPI)
        png = png[0] if isinstance(png, list) else png
        out.parent.mkdir(parents=True, exist_ok=True)
        for old in out.parent.glob(f"{stem}-*.png"):
            old.unlink(missing_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(png)
        tmp.replace(out)
        return out
