"""Render cache: ``(recipe, code_hash, params, w, h)`` → saved render (SPEC §8)."""

from __future__ import annotations

import json
import threading
from typing import Optional

from .project import Project
from .recipes import RenderRequest, RenderResult

INDEX = ".pintu/renders.json"


def cache_key(req: RenderRequest, code_hash: str) -> str:
    params = json.dumps(req.params or {}, sort_keys=True, separators=(",", ":"), default=str)
    return f"{req.recipe}|{code_hash}|{params}|{req.width_mm:.2f}|{req.height_mm:.2f}"


class RenderCache:
    """Index of successful renders, kept in memory and in ``.pintu/renders.json``."""

    def __init__(self, project: Project):
        self.project = project
        self.path = project.root / INDEX
        self._lock = threading.Lock()
        try:
            self._index: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._index = {}

    def get(self, req: RenderRequest, code_hash: Optional[str]) -> Optional[RenderResult]:
        """The cached result, if its SVG is still on disk."""
        if code_hash is None:
            return None
        e = self._index.get(cache_key(req, code_hash))
        if e is None or not (self.project.root / e["svg"]).is_file():
            return None
        return RenderResult(ok=True, svg=e["svg"], code_hash=code_hash, summary=e.get("summary"), cached=True)

    def put(self, req: RenderRequest, res: RenderResult) -> None:
        """Records a successful render; drops older entries for the same SVG file."""
        if not res.ok or not res.code_hash:
            return
        with self._lock:
            for k in [k for k, e in self._index.items() if e["svg"] == res.svg]:
                del self._index[k]
            self._index[cache_key(req, res.code_hash)] = {"svg": res.svg, "summary": res.summary}

    def save(self) -> None:
        """Writes the index file."""
        with self._lock:
            text = json.dumps(self._index)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
