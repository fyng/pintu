"""Recipe render interface (SPEC §8).

A runner renders ``module:function`` at a cell size in the project's recipe
env and returns a ``RenderResult``. ``SubprocessRunner`` starts one process per
render; the kernel runner implements the same ``Runner`` protocol. Both run
``worker.py`` in the recipe env.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from . import style as styles, worker
from .project import Project

OUT_DIR = "pintu_out"
WORKER = Path(__file__).with_name("worker.py")
WORKER_MARKER = "PINTU_RESULT "  # matches worker.MARKER


@dataclass(frozen=True)
class RenderRequest:
    """One recipe call.

    Attributes:
        recipe: ``module.path:function``, importable from the project root.
        params: JSON-serializable keyword arguments.
        width_mm: Width of the cell.
        height_mm: Height of the cell below its letter band.
        multiples: For a multiples panel, the ``mosaic``, ``share`` and ratios
            passed to the recipe, and the style's ``margins`` for the SDK.
        preset: The board's style preset, for its fonts and lint; None: the pack's default.
        rc: matplotlib rcParams the kernel applies for this render (the preset's merged
            ``[matplotlib] rc``); None if the pack sets none.
    """

    recipe: str
    params: dict = field(default_factory=dict)
    width_mm: float = 0.0
    height_mm: float = 0.0
    multiples: Optional[dict] = None
    preset: Optional[str] = None
    rc: Optional[dict] = None


@dataclass
class RenderResult:
    """Outcome of one render.

    Attributes:
        ok: Whether the recipe returned a Figure and the SVG was saved.
        svg: Root-relative SVG path, or None on failure.
        error: Traceback text on failure.
        code_hash: sha256 of the recipe module and its project-local imports.
        summary: ``worker.summarize`` output: size, axes, texts, overflow, fonts, marks.
        stdout: Captured recipe stdout.
        stderr: Captured recipe stderr.
        seconds: Wall time of the call.
        cached: Whether the result came from the render cache.
        lint: ``lint.check`` issues, set by ``Renders``.
    """

    ok: bool
    svg: Optional[str] = None
    error: Optional[str] = None
    code_hash: Optional[str] = None
    summary: Optional[dict] = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    cached: bool = False
    lint: list = field(default_factory=list)


class Runner(Protocol):
    """Renders recipes in the project's recipe env."""

    async def render(self, req: RenderRequest) -> RenderResult: ...

    async def close(self) -> None: ...


def param_hash(params: dict) -> str:
    """Stable 12-hex-digit hash of a params dict."""
    blob = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def output_path(recipe: str, params: dict, w: float, h: float, multiples: Optional[dict] = None,
                rc: Optional[dict] = None) -> str:
    """Root-relative ``pintu_out/<recipe>/<param-hash>/<w>x<h>.svg``; the hash covers ``multiples`` and ``rc``."""
    safe = recipe.replace(":", ".")
    key = {**params, "__multiples__": multiples} if multiples else params
    key = {**key, "__rc__": rc} if rc else key
    return f"{OUT_DIR}/{safe}/{param_hash(key)}/{_fmt(w)}x{_fmt(h)}.svg"


def request_path(req: RenderRequest) -> str:
    """``output_path`` of a request."""
    return output_path(req.recipe, req.params, req.width_mm, req.height_mm, req.multiples, req.rc)


def recipe_python(project: Project) -> str:
    """Interpreter of the recipe env: ``[kernel] python`` in pintu.toml.

    Falls back to the backend's own interpreter, which suits dev and tests.
    """
    return str(project.settings.get("kernel", {}).get("python") or sys.executable)


def worker_request(project: Project, req: RenderRequest) -> dict:
    """The dict ``worker.render`` takes."""
    pack = styles.for_project(project).view(req.preset)
    return {"root": str(project.root), "recipe": req.recipe, "params": req.params,
            "width_mm": req.width_mm, "height_mm": req.height_mm, "multiples": req.multiples,
            "out": str(project.root / request_path(req)),
            "fonts": list(pack.fonts), "font_paths": [str(p) for p in pack.font_paths],
            "rc": req.rc or {}}


def to_result(raw: dict, req: RenderRequest, stdout: str = "", stderr: str = "") -> RenderResult:
    """Builds a RenderResult from a ``worker.render`` result dict."""
    return RenderResult(
        ok=raw["ok"],
        svg=request_path(req) if raw["ok"] else None,
        error=raw["error"], code_hash=raw["code_hash"], summary=raw["summary"],
        stdout=stdout, stderr=stderr, seconds=raw["seconds"])


class SubprocessRunner:
    """Runs each render in a fresh recipe-env process (the §12.1 P2 fallback)."""

    def __init__(self, project: Project, timeout: float = 120.0):
        self.project = project
        self.timeout = timeout

    async def render(self, req: RenderRequest) -> RenderResult:
        """Renders one recipe call; never raises for recipe errors."""
        payload = json.dumps(worker_request(self.project, req))
        proc = await asyncio.create_subprocess_exec(
            recipe_python(self.project), str(WORKER), payload, cwd=str(self.project.root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return RenderResult(ok=False, error=f"render timed out after {self.timeout:.0f} s")
        text, errtext = out.decode(errors="replace"), err.decode(errors="replace")
        head, sep, tail = text.rpartition(WORKER_MARKER)
        if not sep:
            return RenderResult(ok=False, error=f"worker exited {proc.returncode}\n{errtext}",
                                stdout=text, stderr=errtext)
        return to_result(json.loads(tail), req, stdout=head, stderr=errtext)

    async def close(self) -> None:
        """Nothing to release."""


def module_file(project: Project, recipe: str) -> Optional[Path]:
    """The recipe module's source file under the project root, or None."""
    return worker.module_path(str(project.root), recipe.partition(":")[0])


def code_hash(project: Project, recipe: str) -> Optional[str]:
    """Hash of the recipe module and its project-local imports, as the worker computes it."""
    return worker.code_hash(str(project.root), recipe.partition(":")[0])


def locate(project: Project, recipe: str) -> Optional[tuple[Path, int]]:
    """The file and 1-based line of the recipe's ``def``, or None if it is missing.

    Resolves ``Class.method`` paths and top-level assignments without importing.
    A file that does not parse gives the syntax error's line, so the render
    reports the error rather than a missing recipe.
    """
    f = module_file(project, recipe)
    name = recipe.partition(":")[2]
    if f is None or not name:
        return None
    try:
        body = ast.parse(f.read_bytes(), filename=str(f)).body
    except SyntaxError as e:
        return f, e.lineno or 1
    node = None
    for part in name.split("."):
        node = next((n for n in body if _defines(n, part)), None)
        if node is None:
            return None
        body = getattr(node, "body", [])
    return f, node.lineno


def _defines(node: ast.AST, name: str) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    return False
