"""Recipe panel renders: what to render, the queue and per-panel status (SPEC §7, §8).

``sync`` compares each recipe panel's wanted render (recipe, params, cell size,
module hash) with the last one. A cache hit is taken at once; a miss goes on a
queue that holds only the latest request per panel. One task drains the queue
through the runner, one render at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from . import lint, style as styles
from .board import Board
from .cache import RenderCache
from .project import Project
from . import multiples as mult
from . import style as styles
from .recipes import RenderRequest, RenderResult, Runner, code_hash, locate, request_path

Key = tuple[str, str]  # (board name, panel id)


@dataclass
class PanelRender:
    """Render state of one recipe panel.

    Attributes:
        want: The render the panel needs now.
        hash: Module hash ``want`` was checked against.
        svg: Root-relative path of the last good render, shown on the board.
        status: "rendering", "ok", "error" or "missing" (recipe not found).
        result: The last runner or cache result.
    """

    want: Optional[RenderRequest] = None
    hash: Optional[str] = None
    svg: Optional[str] = None
    status: str = "rendering"
    result: Optional[RenderResult] = None


def request_for(board: Board, panel: dict) -> Optional[RenderRequest]:
    """The render a recipe panel needs below its letter band, or None for other panels.

    A multiples panel's request carries its mosaic, share and ratios, and the style's margins.
    """
    src = panel.get("source") or {}
    if "recipe" not in src:
        return None
    _, _, w, h = board.content_rect(panel)
    params = json.loads(json.dumps(src.get("params") or {}, default=str))
    m = src.get("multiples")
    m = {**mult.call_kwargs(m), "margins": styles.margins(board.preset)} if isinstance(m, dict) else None
    return RenderRequest(str(src["recipe"]), params, round(w, 2), round(h, 2), m)


class Renders:
    """Schedules recipe panel renders for the open boards.

    Args:
        project: The project.
        runner: Renders recipes; called one request at a time.
        on_done: Awaited with the board name after a panel's render lands.
        save: Called after the cache changes, to persist it (e.g. on a writer thread).
    """

    def __init__(self, project: Project, runner: Runner, on_done: Callable[[str], Awaitable[None]],
                 save: Callable[[], None] = lambda: None):
        self.project = project
        self.pack = styles.for_project(project)
        self.runner = runner
        self.cache = RenderCache(project)
        self.fonts_found: Optional[list] = None  # pack fonts matplotlib found, from the latest render
        self.on_done = on_done
        self.save = save
        self.panels: dict[Key, PanelRender] = {}
        self._queue: dict[Key, RenderRequest] = {}
        self._wake: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Starts the queue task on the running loop."""
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        """Stops the queue task and closes the runner."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.runner.close()

    def get(self, name: str, pid: str) -> Optional[PanelRender]:
        return self.panels.get((name, pid))

    def shown(self, name: str, panel: dict) -> Optional[str]:
        """The SVG to draw for a recipe panel: its last good render."""
        st = self.panels.get((name, panel["id"]))
        return st.svg if st else None

    def _latest(self, req: RenderRequest) -> Optional[str]:
        """Newest earlier render of this recipe and params, at any size."""
        d = (self.project.root / request_path(req)).parent
        svgs = sorted(d.glob("*.svg"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
        return self.project.relative(svgs[-1]) if svgs else None

    def sync(self, name: str, board: Board) -> bool:
        """Queues renders for a board's recipe panels that changed. Returns whether any state changed."""
        changed, live = False, set()
        for p in board.panels:
            req = request_for(board, p)
            if req is None:
                continue
            key = (name, p["id"])
            live.add(key)
            st = self.panels.get(key)
            if st is None:
                st = self.panels[key] = PanelRender(svg=self._latest(req))
            h = code_hash(self.project, req.recipe)
            missing = locate(self.project, req.recipe) is None
            gone = st.status == "ok" and not (st.svg and (self.project.root / st.svg).is_file())
            if req == st.want and h == st.hash and missing == (st.status == "missing") and not gone:
                continue
            changed = True
            st.want, st.hash = req, h
            self._queue.pop(key, None)
            if missing:
                st.status = "missing"
                continue
            hit = self.cache.get(req, h)
            if hit:
                st.svg, st.status, st.result = hit.svg, "ok", self._lint(req, hit)
                continue
            st.status = "rendering"
            self._queue[key] = req
            if self._wake:
                self._wake.set()
        for key in [k for k in self.panels if k[0] == name and k not in live]:
            del self.panels[key]
            self._queue.pop(key, None)
            changed = True
        return changed

    async def render(self, req: RenderRequest) -> RenderResult:
        """Renders one request through the cache, then the runner."""
        hit = self.cache.get(req, code_hash(self.project, req.recipe))
        if hit:
            return self._lint(req, hit)
        res = await self.runner.render(req)
        if res.ok:
            self.cache.put(req, res)
            self.save()
            if "fonts_found" in (res.summary or {}):
                self.fonts_found = res.summary["fonts_found"]
        return self._lint(req, res)

    def _lint(self, req: RenderRequest, res: RenderResult) -> RenderResult:
        """Sets ``res.lint`` from its summary at the requested size."""
        if res.ok:
            res.lint = lint.check(res.summary, req.width_mm, req.height_mm, self.pack)
        return res

    async def _drain(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            key = next(iter(self._queue))
            req = self._queue.pop(key)
            res = await self.render(req)
            st = self.panels.get(key)
            if st is None or st.want != req or key in self._queue:
                continue
            st.result = res
            st.status = "ok" if res.ok else "error"
            if res.ok:
                st.svg = res.svg
            await self.on_done(key[0])


def file_version(project: Project, rel: Optional[str]) -> Optional[int]:
    """Modification time of a root-relative file in ns, for cache-busting URLs."""
    if not rel:
        return None
    with contextlib.suppress(OSError):
        return (project.root / Path(rel)).stat().st_mtime_ns
    return None
