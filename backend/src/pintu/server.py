"""FastAPI app: board REST API, preview push over WebSocket, file browser, recipe renders."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import shlex
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from watchfiles import PythonFilter, awatch

from . import codegen, gallery, multiples as mult, style as styles
from .board import Board, BoardError
from .catalog import Catalog
from .kernel import KernelRunner
from .project import BOARD_SUFFIX, IMAGE_KINDS, PathError, Project
from .recipes import Runner, locate, module_file
from .render import Renderer
from .renders import Renders, file_version

log = logging.getLogger("pintu")
STATIC = Path(__file__).parent / "static"


class NewBoard(BaseModel):
    name: str
    width: float = 183
    height: float = 170
    grid: int = 36
    gutter: float = 3


class Ops(BaseModel):
    ops: list[dict[str, Any]]


class RecipeRef(BaseModel):
    recipe: str


class State:
    """Open boards, their revisions and connected clients."""

    def __init__(self, project: Project, runner: Optional[Runner] = None):
        self.project = project
        self.boards: dict[str, Board] = {}
        self.rev: dict[str, int] = {}
        # Board text pintu holds as current, and its board writes not yet on disk.
        self.text: dict[str, str] = {}
        self.pending: collections.Counter = collections.Counter()
        self._pending_lock = threading.Lock()
        # One writer thread keeps disk writes ordered and off the preview path.
        self.writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pintu-writer")
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()
        self.pack = styles.for_project(project)
        self.renders = Renders(project, runner or KernelRunner(project), self._rendered,
                               save=lambda: self.writer.submit(self.renders.cache.save))
        self.renderer = Renderer(project, recipe_svg=self.renders.shown, pack=self.pack)
        self._meta: dict[tuple, Optional[dict]] = {}

    def multiples_meta(self, recipe: str) -> Optional[dict]:
        """``item`` and ``min_cell`` of a ``@multiples`` recipe, cached by file mtime; None for others."""
        return recipe_multiples(self.project, recipe, self._meta)

    def get(self, name: str) -> Board:
        if name not in self.boards:
            path = self.project.board_path(name)
            if not path.exists():
                raise HTTPException(404, f"no board {name!r}")
            self.boards[name] = Board.load(path, self.pack)
            self.rev[name] = 1
            self.renders.sync(name, self.boards[name])
        return self.boards[name]

    def save(self, name: str, board: Board) -> None:
        """Takes the board as current and queues its file write."""
        text = board.dumps()
        self.text[name] = text
        with self._pending_lock:
            self.pending[name] += 1
        self.boards[name] = board
        self.rev[name] = self.rev.get(name, 0) + 1
        self.writer.submit(self._write, name, text)

    def _write(self, name: str, text: str) -> None:
        try:
            self.project.board_path(name).write_text(text, encoding="utf-8")
        finally:
            with self._pending_lock:
                self.pending[name] -= 1

    def file_ok(self, rel: str) -> bool:
        try:
            return self.project.resolve(rel).is_file()
        except PathError:
            return False

    def view(self, name: str) -> dict:
        """Board state for the frontend."""
        b = self.get(name)
        page = b.page
        st = b.preset
        letters = b.letters()
        bands = b.bands()
        panels, warnings = [], styles.font_problems(self.pack, self.renders.fonts_found)
        if page.height > st["max_height"]:
            warnings.append(f"page height {page.height:g} mm exceeds the {st['name']} cap of {st['max_height']:g} mm")
        for p in b.panels:
            f, kind, label = codegen.panel_source(p, self.file_ok, lambda q: self.renders.shown(name, q))
            src = dict(p.get("source") or {})
            if f is None and label != p["id"] and "recipe" not in src:
                warnings.append(label)
            grp = b.group_of(p["id"])
            setting = (grp or p).get("letter", "auto")
            panels.append({
                "id": p["id"],
                "cell": list(p["cell"]),
                "rect": list(page.rect(p["cell"])),
                "band": bands[p["id"]],
                "letter": letters[p["id"]].lower() if letters[p["id"]] and st["letter"]["lower"] else letters[p["id"]],
                "letterSetting": None if setting is None or setting is False else setting,
                "source": {k: src[k] for k in ("file", "recipe") if k in src},
                "file": f,
                "fileVersion": file_version(self.project, f),
                "kind": kind if f else None,
                "group": grp["id"] if grp else None,
                **({"params": src.get("params") or {}, "render": self.render_view(name, p["id"]),
                    **self.multiples_view(b, p)}
                   if "recipe" in src else {}),
            })
        units = b.unit_letters()
        groups = [{"id": g["id"], "panels": list(g["panels"]), "cell": list(b.group_cell(g)),
                   "rect": list(page.rect(b.group_cell(g))),
                   "letter": units[g["id"]].lower() if units[g["id"]] and st["letter"]["lower"] else units[g["id"]],
                   "letterSetting": None if g.get("letter", "auto") in (None, False) else g.get("letter", "auto")}
                  for g in b.groups]
        return {
            "name": name,
            "rev": self.rev[name],
            "page": {"width": page.width, "height": page.height, "grid": [page.nx, page.ny],
                     "gutter": page.gutter, "style": b.style},
            "preset": {"pack": self.pack.name, "name": st["name"], "widths": st["widths"], "maxHeight": st["max_height"],
                       "letterBand": b.letter_band},
            "panels": panels,
            "groups": groups,
            "warnings": warnings,
        }

    def multiples_view(self, b: Board, p: dict) -> dict:
        """A recipe panel's multiples state: the recipe's item param, and for a multiples panel its
        mosaic, share, ratios, ``min_cell``, mean cell size in mm and any reflow on offer."""
        src = p["source"]
        meta = self.multiples_meta(str(src["recipe"]))
        out: dict[str, Any] = {"multiplesItem": meta["item"] if meta else None}
        m = src.get("multiples")
        if isinstance(m, dict):
            kw = mult.call_kwargs(m)
            nrows, ncols, _ = mult.parse(kw["mosaic"])
            margins = styles.margins(b.preset)
            _, _, w, h = b.content_rect(p)
            min_cell = meta["min_cell"] if meta else None
            out["multiples"] = {
                "item": str(m["item"]), **kw, "minCell": min_cell,
                "cellMm": [round(v, 2) for v in mult.cell_size(w, h, nrows, ncols, margins, kw["share"])],
                "reflow": mult.reflow(m, w, h, min_cell, margins),
            }
        return out

    def render_view(self, name: str, pid: str) -> dict:
        """Render status of a recipe panel for the frontend."""
        st = self.renders.get(name, pid)
        if st is None:
            return {"status": "rendering"}
        res = st.result
        out = {"status": st.status}
        if res is not None:
            out.update(seconds=round(res.seconds, 3), cached=res.cached, lint=res.lint)
            if st.status == "error":
                out.update(error=res.error, stdout=res.stdout[-4000:], stderr=res.stderr[-4000:])
        return out

    async def _rendered(self, name: str) -> None:
        async with self.lock:
            if name in self.boards:
                await self.publish(name)

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                self.clients.discard(ws)

    async def publish(self, name: str) -> None:
        """Queues recipe renders, pushes board state and the compiled SVG preview, then writes the PDF."""
        self.renders.sync(name, self.boards[name])
        await self.broadcast({"type": "board", "board": self.view(name)})
        rev, board = self.rev[name], self.boards[name]
        try:
            svg, src, ms = await asyncio.to_thread(self.renderer.svg, name, board)
        except Exception as e:
            await self.broadcast({"type": "error", "name": name, "message": f"typst: {e}"})
            return
        await self.broadcast({"type": "preview", "name": name, "rev": rev, "ms": round(ms, 2),
                              "svg": svg.decode("utf-8")})
        self.writer.submit(self.renderer.write, name, src)


def _check_file(project: Project, rel: Optional[str]) -> Optional[str]:
    if rel is None:
        return None
    path = project.resolve(rel)
    if not path.is_file():
        raise BoardError(f"not a file: {rel}")
    if path.suffix.lower() not in IMAGE_KINDS:
        raise BoardError(f"unsupported file type: {rel}")
    return project.relative(path)


def apply_ops(project: Project, board: Board, ops: list[dict]) -> list[str]:
    """Applies edits to a board in place. Returns warnings.

    Raises:
        BoardError: An edit is invalid.
    """
    warnings = []
    for op in ops:
        kind = op.get("op")
        if kind == "set_cell":
            board.set_cell(op["id"], op["cell"])
        elif kind == "add":
            pid = op.get("id") or (recipe_panel_id(project, str(op["recipe"]), op.get("params") or {})
                                   if op.get("recipe") else None)
            pid = board.add_panel(op["cell"], _check_file(project, op.get("file")), pid)
            if op.get("recipe"):
                board.set_recipe(pid, str(op["recipe"]), op.get("params"))
        elif kind == "remove":
            board.remove_panel(op["id"])
        elif kind == "set_source":
            board.set_source_file(op["id"], _check_file(project, op.get("file")))
        elif kind == "set_recipe":
            board.set_recipe(op["id"], str(op["recipe"]), op.get("params"))
        elif kind == "set_letter":
            board.set_letter(op["id"], op.get("letter"))
        elif kind == "set_multiples":
            board.set_multiples(op["id"], **{k: op[k] for k in
                                             ("item", "mosaic", "share", "width_ratios", "height_ratios") if k in op})
        elif kind == "group":
            board.add_group(list(op["ids"]), op.get("group"))
        elif kind == "ungroup":
            board.ungroup(op["group"])
        elif kind == "split":
            ids, exact = board.split(op["id"], int(op["n"]), op.get("axis", "x"))
            if not exact:
                warnings.append(f"split of {op['id']} into {op['n']} is not exact; parts differ by one grid unit")
        elif kind == "set_page":
            board.set_page(**{k: op[k] for k in ("width", "height", "grid", "gutter", "style") if k in op})
        else:
            raise BoardError(f"unknown op {kind!r}")
    board.validate()
    return warnings


def recipe_multiples(project: Project, recipe: str, memo: Optional[dict] = None) -> Optional[dict]:
    """``multiples.recipe_meta`` of a recipe, memoised by module path and mtime."""
    f = module_file(project, recipe)
    try:
        key = (recipe, str(f), f.stat().st_mtime_ns) if f else None
    except OSError:
        key = None
    if key is None:
        return None
    if memo is None or key not in memo:
        meta = mult.recipe_meta(f, recipe.partition(":")[2])
        if memo is None:
            return meta
        memo[key] = meta
    return memo[key]


def recipe_panel_id(project: Project, recipe: str, params: dict) -> str:
    """A readable id for a new recipe panel: the function name, plus the item param's value
    (a ``@multiples`` recipe) or the only param's value."""
    fn = recipe.partition(":")[2].rsplit(".", 1)[-1] or "panel"
    meta = recipe_multiples(project, recipe)
    if meta and meta["item"] in params:
        return f"{fn}-{params[meta['item']]}"
    return f"{fn}-{next(iter(params.values()))}" if len(params) == 1 else fn


def open_command(path: Path, line: int) -> Optional[list[str]]:
    """Command that opens a file at a line in the user's editor: ``code -g``, else ``$EDITOR``."""
    code = shutil.which("code")
    if code:
        return [code, "-g", f"{path}:{line}"]
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    return [*shlex.split(editor), f"+{line}", str(path)] if editor else None


def create_app(project: Project, dev: bool = False, watch: bool = True, runner: Optional[Runner] = None) -> FastAPI:
    """Builds the app for a project.

    Args:
        project: The open project.
        dev: Skip serving the built frontend (Vite serves it).
        watch: Reload boards when their files change on disk, and re-render
            recipe panels when Python files change.
        runner: Recipe runner; defaults to a ``KernelRunner``.
    """
    state = State(project, runner)
    catalog = Catalog(project)

    async def watcher() -> None:
        project.boards_dir.mkdir(exist_ok=True)
        async for changes in awatch(project.boards_dir, recursive=False):
            for _, p in changes:
                if not p.endswith(BOARD_SUFFIX):
                    continue
                name = Path(p).name[: -len(BOARD_SUFFIX)]
                path = Path(p)
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8")
                # Skip pintu's own writes: the current text, or older text while newer writes are queued.
                if text == state.text.get(name) or state.pending[name] > 0:
                    continue
                async with state.lock:
                    try:
                        board = Board.loads(text, state.pack)
                    except BoardError as e:
                        await state.broadcast({"type": "error", "name": name, "message": str(e)})
                        continue
                    state.text[name] = text
                    state.boards[name] = board
                    state.rev[name] = state.rev.get(name, 0) + 1
                    await state.publish(name)

    async def recipe_watcher() -> None:
        ignore = [project.root / d for d in (".pintu", "pintu_out", "boards")]
        async for _ in awatch(project.root, watch_filter=PythonFilter(ignore_paths=ignore)):
            async with state.lock:
                for name, board in list(state.boards.items()):
                    if state.renders.sync(name, board):
                        await state.publish(name)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        state.renders.start()
        tasks = [asyncio.create_task(watcher()), asyncio.create_task(recipe_watcher()),
                 asyncio.create_task(gallery.watch(catalog, state.broadcast))] if watch else []
        yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await state.renders.stop()
        state.writer.shutdown(wait=True)
        catalog.close()

    app = FastAPI(title="pintu", lifespan=lifespan)
    app.state.pintu = state
    app.include_router(gallery.router(catalog))

    @app.get("/api/project")
    def get_project():
        return {"name": project.root.name, "boards": sorted(set(project.board_names()) | set(state.boards))}

    @app.post("/api/boards")
    async def new_board(req: NewBoard):
        try:
            path = project.board_path(req.name)
        except PathError as e:
            raise HTTPException(400, str(e))
        if path.exists():
            raise HTTPException(409, f"board {req.name!r} exists")
        project.boards_dir.mkdir(exist_ok=True)
        async with state.lock:
            state.save(req.name, Board.new(req.width, req.height, req.grid, req.gutter, state.pack))
        return state.view(req.name)

    @app.get("/api/boards/{name}")
    async def get_board(name: str):
        try:
            return state.view(name)
        except (BoardError, PathError) as e:
            raise HTTPException(400, str(e))

    @app.post("/api/boards/{name}/ops")
    async def post_ops(name: str, req: Ops):
        async with state.lock:
            try:
                board = Board.loads(state.get(name).dumps(), state.pack)
                warnings = apply_ops(project, board, req.ops)
            except (BoardError, PathError, KeyError, TypeError, ValueError) as e:
                raise HTTPException(400, str(e))
            state.save(name, board)
            await state.publish(name)
        return {**state.view(name), "opWarnings": warnings}

    @app.get("/api/boards/{name}/preview.svg")
    async def preview_svg(name: str):
        svg, _, _ = await asyncio.to_thread(state.renderer.svg, name, state.get(name))
        return Response(svg, media_type="image/svg+xml")

    @app.get("/api/boards/{name}/preview.pdf")
    async def preview_pdf(name: str):
        src = state.renderer.source(name, state.get(name))
        pdf = await asyncio.to_thread(state.renderer.compile, src, "pdf")
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})

    def _locate(recipe: str) -> tuple[Path, int]:
        where = locate(project, recipe)
        if where is None:
            raise HTTPException(404, f"recipe {recipe!r} not found under the project root")
        return where

    @app.get("/api/recipes/locate")
    def recipe_locate(recipe: str):
        path, line = _locate(recipe)
        return {"recipe": recipe, "file": project.relative(path), "line": line,
                "text": path.read_text(encoding="utf-8", errors="replace")}

    @app.post("/api/recipes/open")
    def recipe_open(req: RecipeRef):
        path, line = _locate(req.recipe)
        cmd = open_command(path, line)
        if cmd is None:
            return {"opened": False, "command": None}
        try:
            subprocess.Popen(cmd, cwd=project.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            raise HTTPException(500, f"cannot run {cmd[0]}: {e}")
        return {"opened": True, "command": shlex.join(cmd)}

    @app.get("/api/files")
    def list_files(path: str = ""):
        try:
            return {"path": path, "entries": project.list_dir(path)}
        except PathError as e:
            raise HTTPException(400, str(e))

    def _file(path: str) -> Path:
        try:
            p = project.resolve(path)
        except PathError as e:
            raise HTTPException(400, str(e))
        if not p.is_file():
            raise HTTPException(404, "not found")
        return p

    @app.get("/api/raw")
    def raw(path: str):
        return FileResponse(_file(path))

    @app.get("/api/thumb")
    async def thumb(path: str):
        p = _file(path)
        if p.suffix.lower() != ".pdf":
            return FileResponse(p)
        try:
            svg = await asyncio.to_thread(state.renderer.thumb, path)
        except Exception as e:
            raise HTTPException(422, f"cannot render {path}: {e}")
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.websocket("/api/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        state.clients.add(sock)
        try:
            while True:
                msg = await sock.receive_json()
                if msg.get("type") == "open" and msg.get("name"):
                    name = msg["name"]
                    try:
                        state.get(name)
                    except HTTPException:
                        continue
                    svg, _, ms = await asyncio.to_thread(state.renderer.svg, name, state.boards[name])
                    await sock.send_json({"type": "preview", "name": name, "rev": state.rev[name],
                                          "ms": round(ms, 2), "svg": svg.decode("utf-8")})
        except WebSocketDisconnect:
            pass
        finally:
            state.clients.discard(sock)

    if not dev and (STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    elif not dev:
        @app.get("/", response_class=HTMLResponse)
        def no_frontend():
            return "<p>pintu: frontend not built. Run <code>npm run build</code> in frontend/.</p>"

    return app
