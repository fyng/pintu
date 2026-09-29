"""Compiles boards and file thumbnails with typst-py."""

from __future__ import annotations

import threading
import time
from importlib import resources
from pathlib import Path
from typing import Callable, Optional

import typst

from . import codegen
from .board import Board
from .project import PathError, Project


def library_source() -> str:
    """The board.typ assembly library shipped with the package."""
    res = resources.files("pintu") / "typst" / "board.typ"
    if res.is_file():
        return res.read_text(encoding="utf-8")
    # Editable install: the library lives at the repo root.
    return (Path(__file__).resolve().parents[3] / "typst" / "board.typ").read_text(encoding="utf-8")


def _first_page(out) -> bytes:
    return out[0] if isinstance(out, list) else out


class Renderer:
    """Builds boards into SVG previews and boards/build/<name>.typ and .pdf.

    One typst Compiler is kept per project, so fonts and files stay cached
    between compiles. Previews compile from memory; only `write` touches disk.

    Args:
        project: The project.
        recipe_svg: ``(board name, panel) -> SVG path`` of a recipe panel's render.
    """

    def __init__(self, project: Project, recipe_svg: Optional[Callable[[str, dict], Optional[str]]] = None):
        self.project = project
        self.recipe_svg = recipe_svg
        self._compiler = typst.Compiler(root=str(project.root))
        self._lock = threading.Lock()
        self._thumbs: dict[tuple[str, float], bytes] = {}
        self._write_library()

    def _exists(self, rel: str) -> bool:
        try:
            return self.project.resolve(rel).is_file()
        except PathError:
            return False

    def _write_library(self) -> None:
        self.project.build_dir.mkdir(parents=True, exist_ok=True)
        lib = self.project.build_dir / codegen.LIBRARY
        text = library_source()
        if not lib.exists() or lib.read_text(encoding="utf-8") != text:
            lib.write_text(text, encoding="utf-8")

    def source(self, name: str, board: Board) -> str:
        """The generated Typst source for a board."""
        shown = (lambda p: self.recipe_svg(name, p)) if self.recipe_svg else None
        return codegen.generate(board, self._exists, name, shown)

    def compile(self, source: str, fmt: str = "svg") -> bytes:
        """Compiles generated source to SVG (first page) or PDF."""
        with self._lock:
            return _first_page(self._compiler.compile(input=source.encode("utf-8"), format=fmt))

    def svg(self, name: str, board: Board) -> tuple[bytes, str, float]:
        """Compiles a board to SVG. Returns (svg, source, milliseconds)."""
        t = time.perf_counter()
        src = self.source(name, board)
        return self.compile(src), src, (time.perf_counter() - t) * 1000

    def write(self, name: str, source: str) -> Path:
        """Writes boards/build/<name>.typ and compiles it to <name>.pdf. Returns the PDF path."""
        self._write_library()
        (self.project.build_dir / f"{name}.typ").write_text(source, encoding="utf-8")
        pdf = self.project.build_dir / f"{name}.pdf"
        pdf.write_bytes(self.compile(source, "pdf"))
        return pdf

    def thumb(self, rel: str) -> Optional[bytes]:
        """An SVG of a PDF file at its own size, cached by modification time."""
        path = self.project.resolve(rel)
        key = (rel, path.stat().st_mtime)
        if key not in self._thumbs:
            doc = ('#set page(width: auto, height: auto, margin: 0pt)\n'
                   f'#image({codegen.typst_str("/" + rel)})\n')
            with self._lock:
                self._thumbs[key] = _first_page(self._compiler.compile(input=doc.encode(), format="svg"))
        return self._thumbs[key]
