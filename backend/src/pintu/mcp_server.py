"""MCP server: lets terminal agents drive a board (SPEC §9).

Tools ``get_board``, ``set_cell``, ``render_panel`` and ``lint``, plus the read-only
resources ``pintu://boards`` and ``pintu://boards/{name}``. Two transports share the
same tools on a server ``State``:

- streamable HTTP at ``/mcp`` on the running ``pintu serve`` (the open board: edits
  reach the browser at once, renders share its kernel);
- stdio, ``pintu mcp --project .``: a headless ``State`` of its own; board edits land in
  the board file, which a running ``pintu serve`` reloads through its file watch.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import agent as ag
from .board import Board, BoardError
from .project import PathError
from .recipes import RenderRequest
from .renders import request_for

INSTRUCTIONS = """\
pintu lays out the panels of an academic figure on a page grid. A board has panels at grid
cells [x0, y0, x1, y1] (grid-line indices); a recipe panel is drawn by a Python function
f(w, h, **params) at its size in mm. Edit recipe files with your own tools; use these tools
to read and change the layout, render a panel and check its lint."""


def build(state: Any) -> MCPServer:
    """The MCP server for a ``server.State``."""
    mcp = MCPServer("pintu", instructions=INSTRUCTIONS)

    def _board(name: str) -> Board:
        try:
            return state.get(name)
        except Exception as e:
            raise ToolError(getattr(e, "detail", None) or str(e))

    async def render(name: str, pid: str, w: Optional[float] = None, h: Optional[float] = None):
        b = _board(name)
        try:
            req = request_for(b, b.panel(pid))
        except KeyError as e:
            raise ToolError(f"no panel {pid!r}") from e
        if req is None:
            raise ToolError(f"panel {pid!r} has no recipe source")
        req = RenderRequest(req.recipe, req.params, float(w or req.width_mm), float(h or req.height_mm), req.multiples)
        return await state.renders.render(req), req

    @mcp.tool()
    def get_board(board: str) -> dict:
        """The board: page size, grid and gutter, and each panel's cell, size in mm (below its
        letter band) and source (file, or recipe and params)."""
        return ag.board_summary(_board(board))

    @mcp.tool()
    async def set_cell(board: str, panel: str, cell: list[int]) -> dict:
        """Move or resize a panel to grid cell [x0, y0, x1, y1]. Returns the panel's new size and
        any "Adapt to size" sessions this started."""
        try:
            out = await state.apply(board, [{"op": "set_cell", "id": panel, "cell": cell}])
        except (BoardError, PathError, KeyError, TypeError, ValueError) as e:
            raise ToolError(f"set_cell refused: {e}")
        p = next(q for q in ag.board_summary(state.get(board))["panels"] if q["id"] == panel)
        return {"panel": p, "warnings": out["warnings"], "adaptSessions": out["adaptSessions"]}

    @mcp.tool()
    async def render_panel(board: str, panel: str, w: Optional[float] = None, h: Optional[float] = None,
                           image: bool = False) -> list:
        """Render a recipe panel (default: its size on the board) and return the lint report and a
        layout summary; with image=true, also a PNG."""
        res, req = await render(board, panel, w, h)
        out: list = [ag.report(res, req.width_mm, req.height_mm, state.pack)]
        if image and res.ok:
            out.append(Image(data=ag.rasterize(state.project.root, res.svg, pack=state.pack), format="png"))
        return out

    @mcp.tool()
    async def lint(board: str, panel: Optional[str] = None) -> dict:
        """Lint issues ({rule, message}) of each recipe panel on the board, or of one panel,
        at its board size; renders it if needed."""
        b = _board(board)
        ids = [panel] if panel else [p["id"] for p in b.panels if "recipe" in (p.get("source") or {})]
        out: dict[str, Any] = {}
        for pid in ids:
            res, _ = await render(board, pid)
            out[pid] = res.lint if res.ok else [{"rule": "render", "message": (res.error or "render failed")[-500:]}]
        return {"board": board, "panels": out}

    @mcp.resource("pintu://boards", mime_type="application/json")
    def boards() -> str:
        """The project's board names."""
        return json.dumps(sorted(set(state.project.board_names()) | set(state.boards)))

    @mcp.resource("pintu://boards/{name}", mime_type="application/json")
    def board_resource(name: str) -> str:
        """A board, as get_board returns it."""
        return json.dumps(ag.board_summary(_board(name)), default=str)

    return mcp


def run_stdio(project) -> None:
    """Serves the tools over stdio on a headless ``State`` of the project (``pintu mcp``)."""
    import anyio

    from .server import State

    async def main() -> None:
        state = State(project)
        state.renders.start()
        try:
            await build(state).run_stdio_async()
        finally:
            await state.renders.stop()
            state.writer.shutdown(wait=True)

    anyio.run(main)
