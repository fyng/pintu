"""Agent loop: context, tools and transcript for recipe and layout edits (SPEC §9)."""

from __future__ import annotations

import asyncio
import base64
import difflib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional, Protocol

import typst

from . import fit as fits, geometry as geo, lint as lints, style as styles
from .board import Board, BoardError
from .catalog import scripts_for
from .llm import text_protocol
from .project import PathError, Project
from .recipes import OUT_DIR, RenderRequest, RenderResult, locate, output_path
from .renders import Renders, request_for
from .server import apply_ops

MAX_STEPS = 20
RECIPE_STEPS = 10  # more steps for a session that turns a static file into a recipe
MAX_IMAGES = 2  # latest renders kept in the history; the endpoint caps 8 per prompt
IMAGE_PX = 1024
MAX_READ = 60_000
MAX_SCRIPT = 20_000
MAX_GREP = 100
SKIP_DIRS = {OUT_DIR, "__pycache__", "node_modules"}
LOG_DIR = ".pintu/agent"

SYSTEM = """\
You are pintu's figure agent. You edit the matplotlib code that draws the panels of a
scientific figure. The user owns the layout: each panel's grid cell, and so its size, is
fixed; get_board reads it, and no tool changes it. A recipe is a Python function
`f(w, h, **params) -> Figure` that draws one panel at exactly its plot area, w x h in mm
(the cell below its letter band); pintu calls it again whenever the user resizes the panel.

Work in small steps: read the recipe, make exact edits with edit_file, then call
render_panel to check the result and the lint report. Keep the recipe signature
`(w, h, ...)` and keep it working at every size: when the form must change with the
size, write a rule on w and/or h (for example `if w >= 120:`), never a hard-coded size.
Paths are relative to the project root. When the task is done and the render is clean,
reply with a one-paragraph summary of what you changed, without calling a tool.
"""

NUDGE = ("Your reply had no tool_call_json block, and nothing has been edited yet. To call a tool, "
         "write the block now. If the task needs no change, reply with the summary again.")

ADAPT = """\
Adapt panel {id} to its new size, {new} (it was designed for {old}). Edit the recipe so
it uses the space well at the new size. Change the form if that serves the plot better:
for example more columns or facets when wide, stacking or moving the legend when narrow,
dropping or shrinking non-essential elements when short, switching orientation when tall
and narrow. Do it with a rule on w and/or h, so the recipe still works at the old size;
never hard-code one size. Render at the new size and at the old size to check, and fix
the lint problems."""

PROMOTE = """\
Promote a scratch plot to a pintu recipe. The script {script} drew {path}{params}, at {size}.
Write a new module {module_file} with a recipe `def {fn}(w, h{sig})` that returns a
matplotlib Figure of exactly w x h mm (`figsize=(w / 25.4, h / 25.4)`) and draws the same
plot as the script does for that file. Take only the part of the script that draws this
plot; turn the values that vary per plot into keyword params with the defaults above; put
slow data loading behind `pintu_sdk.cache(fn, *args)`. Decorate it with
`@pintu_sdk.panel(min_size=(w, h), max_size=(w, h))`, literal tuples in mm giving the range
of sizes the recipe is designed for (a range around {size}, not one size). Do not edit the
script. Create the
module with write_file, then check it with render_recipe at {size} and at half the width,
and fix the lint problems. End your final reply with the line `RECIPE: {recipe}`."""

STATIC = """\
{file} is a static file: it does not change with its cell, and no tool re-runs the script \
that drew it. To change this plot, turn it into a recipe that draws at the plot area:
1. Write a new module {module_file} with a recipe `def {fn}(w, h)` that returns a matplotlib \
Figure of exactly w x h mm (`figsize=(w / 25.4, h / 25.4)`) and draws the same plot as {script} \
does for {file}. Take only the part of the script that draws this plot; do not edit the script.{helpers}
2. Decorate it with `@panel(min_size=(w, h), max_size=(w, h))`, literal tuples in mm giving a \
range of sizes around {size}. Import it with `from pintu_sdk import panel`; the SDK is installed, \
not in the project.
3. Check it with render_recipe at {size}.
4. Call use_recipe with it: the panel then shows the recipe, in the same cell.
5. Make the requested change in the recipe, and check it with render_panel."""

NO_SCRIPT = ("No sidecar names the script that drew {file}, and no project .py file names it. Find the "
             "plot's code with grep; if it is not in the project, say so and stop.")

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file in the project.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Project-relative path"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_dir", "description": "List a project folder.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Project-relative folder; '' for the root"}}}}},
    {"type": "function", "function": {
        "name": "grep", "description": "Search project text files with a Python regex.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Folder or file to search; '' for the root"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace one exact occurrence of old_string with new_string in a file. "
                       "old_string must match exactly once, including whitespace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_string": {"type": "string"},
            "new_string": {"type": "string"}},
            "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {
        "name": "render_panel",
        "description": "Render a panel. A recipe panel: its lint report and layout summary. A static-file "
                       "panel: the file as the board draws it and how much of the plot area it fills. "
                       "Returns the image too, for vision models. Defaults to the panel's plot area.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string"}, "w": {"type": "number", "description": "Width in mm"},
            "h": {"type": "number", "description": "Height in mm"}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "get_board",
        "description": "The board, read-only: page, grid geometry, and each panel's letter, cell, plot area "
                       "and source (for a static file, how it fills its plot area).",
        "parameters": {"type": "object", "properties": {}}}},
]

# Offered to no session: the user owns the layout.
LAYOUT_TOOLS = [
    {"type": "function", "function": {
        "name": "set_cell",
        "description": "Move or resize a panel to grid cell [x0, y0, x1, y1] (grid-line indices).",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string"},
            "cell": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}},
            "required": ["id", "cell"]}}},
]

WRITE_TOOLS = [
    {"type": "function", "function": {
        "name": "write_file", "description": "Create a new text file in the project; refuses to overwrite.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "render_recipe",
        "description": "Render a recipe `module:function` at w x h mm with params; return its lint report and "
                       "layout summary (and the image, for vision models).",
        "parameters": {"type": "object", "properties": {
            "recipe": {"type": "string"}, "w": {"type": "number"}, "h": {"type": "number"},
            "params": {"type": "object"}},
            "required": ["recipe", "w", "h"]}}},
]

PROMOTE_TOOLS = TOOLS + WRITE_TOOLS

# For a session about a static-file panel: write a recipe, then show it in the panel.
STATIC_TOOLS = TOOLS + WRITE_TOOLS + [
    {"type": "function", "function": {
        "name": "use_recipe",
        "description": "Show a recipe `module:function` in this session's panel in place of its static file. "
                       "The cell stays as it is; the recipe must render at the plot area.",
        "parameters": {"type": "object", "properties": {
            "recipe": {"type": "string"}, "params": {"type": "object"}},
            "required": ["recipe"]}}},
]

GEOMETRY = ("Grid line i lies i * pitch_mm from the page's top left. A cell [x0, y0, x1, y1] in grid lines is "
            "cell_mm = [x, y, w, h] with x = x0 * pitch and w = (x1 - x0) * pitch - gutter (the same for y). "
            "A lettered panel reserves band_mm at the top of its cell for the letter; its plot fills the rest, "
            "plot_mm. A static file is fitted into plot_mm with its aspect ratio kept: fit.drawn_mm is its "
            "drawn size, fit.fill the share of plot_mm it covers per axis.")


class Guard(Protocol):
    """Called around each file write, for checkpoints."""

    def before(self, rel: str) -> None: ...

    def after(self, rel: str) -> None: ...


class ToolError(Exception):
    """A tool call the agent should see as an error result."""


def edit_miss(text: str, old: str, cap: int = 30) -> str:
    """Where old most nearly matches text, with line numbers, and how it differs."""
    lines, want = text.splitlines(), old.strip("\n").splitlines()
    n = max(len(want), 1)
    bag = Counter(ln.strip() for ln in want)
    best, start = (-1, -1.0), 0
    for i in range(max(len(lines) - n + 1, 1)):
        win = lines[i:i + n]
        same = sum((Counter(ln.strip() for ln in win) & bag).values())
        r = difflib.SequenceMatcher(None, "\n".join(win), "\n".join(want), autojunk=False).ratio()
        if (same, r) > best:
            best, start = (same, r), i
    best = best[1]
    got = lines[start:start + n]
    if [ln.strip() for ln in got] == [ln.strip() for ln in want]:
        why = "Same text, but whitespace or indentation differs."
    elif sorted(ln.strip() for ln in got) == sorted(ln.strip() for ln in want):
        why = "Same lines, but in a different order."
    else:
        k = next((k for k, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
        why = (f"First difference at line {start + k + 1}: file has {got[k][:120]!r}"
               if k < len(got) else "The file region is shorter.")
        why += f", you sent {want[k][:120]!r}." if k < len(want) else "."
    shown = [f"{start + j + 1:>5}| {ln[:200]}" for j, ln in enumerate(got[:cap])]
    if len(got) > cap:
        shown.append(f"[{len(got) - cap} more lines]")
    return (f"Closest region (lines {start + 1}-{start + len(got)}, {best:.0%} similar):\n"
            + "\n".join(shown) + f"\n{why} Copy old_string exactly from these lines.")


fmt_size = lints.fmt_size
drawn_texts = lints.drawn_texts


def lint(summary: dict, w: float, h: float, pack: Optional[styles.StylePack] = None) -> list[str]:
    """Lint problems of a render summary (SPEC §8) as text lines; see ``lint.check``."""
    return lints.lines(lints.check(summary, w, h, pack))


def overflow(summary: dict) -> list[str]:
    """The overflow lint lines only."""
    return [p for p in lint(summary, *summary["size_mm"]) if p.startswith("overflow")]


def compact(summary: dict) -> dict:
    """The render summary, trimmed for the prompt."""
    axes = [{k: a[k] for k in ("bbox_mm", "title", "xlabel", "ylabel", "legend", "n_xticks", "n_yticks")}
            for a in summary["axes"]]
    drawn = drawn_texts(summary)
    texts = [f"{t['text']!r} {t['fontsize_pt']:g}pt {t['bbox_mm']}" for t in drawn[:60]]
    if len(drawn) > 60:
        texts.append(f"... {len(drawn) - 60} more")
    return {"size_mm": summary["size_mm"], "axes": axes, "texts": texts}


def report(res: RenderResult, w: float, h: float, pack: Optional[styles.StylePack] = None) -> str:
    """Render outcome as text: status, lint and summary."""
    if not res.ok:
        return f"render FAILED at {fmt_size(w, h)}:\n{(res.error or '')[-3000:]}"
    problems = lint(res.summary, w, h, pack)
    lines = [f"render ok at {fmt_size(w, h)} ({res.svg})",
             "lint: " + ("clean" if not problems else f"{len(problems)} problem(s)")]
    lines += [f"- {p}" for p in problems[:30]]
    lines.append("summary (bbox_mm = [x0, y0, x1, y1] from the top-left): " + json.dumps(compact(res.summary)))
    return "\n".join(lines)


def rasterize(root: Path, svg_rel: str, max_px: int = IMAGE_PX, pack: Optional[styles.StylePack] = None) -> bytes:
    """PNG of a root-relative SVG, at most ``max_px`` on the long side, via typst with the pack's fonts."""
    doc = ("#set page(width: auto, height: auto, margin: 0pt, fill: white)\n"
           f'#image("/{svg_rel}")\n')
    svg = (root / svg_rel).read_text(encoding="utf-8")
    m = re.search(r'width="([\d.]+)pt"\s+height="([\d.]+)pt"', svg)
    long_in = max(float(m.group(1)), float(m.group(2))) / 72 if m else 7.0
    ppi = min(300.0, max_px / long_in)
    fonts = styles.typst_fonts((pack or styles.default()).font_paths)
    out = typst.compile(input=doc.encode(), root=str(root), format="png", ppi=ppi, font_paths=fonts)
    return out[0] if isinstance(out, list) else out


def image_part(png: bytes) -> dict:
    """An image content part for Chat Completions."""
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


def prune_images(messages: list[dict], keep: int = MAX_IMAGES) -> list[dict]:
    """A copy of the history with all but the latest ``keep`` images replaced by text."""
    out, seen = [], 0
    for m in reversed(messages):
        if isinstance(m.get("content"), list):
            parts = []
            for p in reversed(m["content"]):
                if p.get("type") == "image_url":
                    seen += 1
                    if seen > keep:
                        p = {"type": "text", "text": "[older render image removed]"}
                parts.append(p)
            m = {**m, "content": parts[::-1]}
        out.append(m)
    return out[::-1]


def _redact(m: dict) -> dict:
    if not isinstance(m.get("content"), list):
        return m
    parts = [{"type": "image_url", "image_url": f"<png, {len(p['image_url']['url'])} base64 chars>"}
             if p.get("type") == "image_url" else p for p in m["content"]]
    return {**m, "content": parts}


async def _no_board(name: str) -> None:
    pass


def local_renders(project: Project) -> Renders:
    """The board's render path (cache, then a recipe kernel) without a running server.

    Call ``stop()`` when done; it shuts the kernel down. The cache is saved after each new render.
    """
    from .kernel import KernelRunner

    renders = Renders(project, KernelRunner(project), _no_board)
    renders.save = renders.cache.save
    return renders


def board_summary(b: Board, project: Optional[Project] = None) -> dict:
    """The board for the agent and MCP: page and grid geometry, and each panel's letter, cell in
    grid lines and mm, letter band, plot area and source; with ``project``, a static file's fit."""
    pg = b.page
    bands, letters = b.bands(), b.letters()
    r = lambda v: round(v, 2)
    panels = []
    for p in b.panels:
        x, y, w, h = pg.rect(p["cell"])
        band = bands[p["id"]]
        src = dict(p.get("source") or {})
        q = {"id": p["id"], "letter": letters[p["id"]], "cell": list(p["cell"]),
             "cell_mm": [r(x), r(y), r(w), r(h)], "band_mm": r(band), "plot_mm": [r(w), r(h - band)],
             "source": {k: src[k] for k in ("file", "recipe", "params") if k in src}}
        f = fits.panel_fit(project, b, p) if project else None
        if f:
            q["fit"] = {k: f[k] for k in ("natural_mm", "drawn_mm", "fill")}
        panels.append(q)
    return {"page": {"width": pg.width, "height": pg.height, "grid": [pg.nx, pg.ny], "gutter": pg.gutter,
                     "pitch_mm": [r(geo.pitch(pg.width, pg.nx, pg.gutter)), r(geo.pitch(pg.height, pg.ny, pg.gutter))],
                     "letter_band_mm": b.letter_band, "style": b.style},
            "geometry": GEOMETRY,
            "layout": "The user owns the layout; cells are fixed.",
            "panels": panels}


def shared_helpers(project: Project, folder: str = "recipes") -> Optional[str]:
    """The import line of the module that most recipes in ``folder`` share (at least two), or None.

    For example ``from .common import axes_mm, figure, RC``.
    """
    names: dict[str, set[str]] = {}
    users: Counter = Counter()
    for f in sorted((project.root / folder).glob("*.py")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for mod, items in re.findall(r"^from \.(\w+) import ([\w, ]+)$", text, re.M):
            users[mod] += 1
            names.setdefault(mod, set()).update(x.strip() for x in items.split(",") if x.strip())
    if not users or users.most_common(1)[0][1] < 2:
        return None
    mod = users.most_common(1)[0][0]
    return f"from .{mod} import {', '.join(sorted(names[mod], key=str.lower))}"


def new_recipe(project: Project, rel: str) -> dict:
    """A free module and recipe name for a recipe that redraws file ``rel``.

    Returns:
        ``fn``, ``module_file`` (``recipes/<fn>.py``, suffixed until free) and ``recipe``.
    """
    stem = re.sub(r"\W+", "_", PurePosixPath(rel).name.split(".")[0]).strip("_").lower() or "plot"
    fn = stem if stem[0].isalpha() else f"plot_{stem}"
    module, k = f"recipes/{fn}.py", 2
    while (project.root / module).exists():
        module, k = f"recipes/{fn}_{k}.py", k + 1
    return {"fn": fn, "module_file": module, "recipe": module[:-3].replace("/", ".") + ":" + fn}


@dataclass
class Result:
    """Outcome of one agent run.

    Attributes:
        text: The model's final message.
        stopped: ``done``, ``step_cap`` or ``aborted``.
        steps: Model calls made.
        usage: Summed token counts.
        seconds: Wall time.
        transcript: Path of the JSONL transcript.
        tool_calls: Names of the tools called, in order.
    """

    text: str
    stopped: str
    steps: int
    usage: dict
    seconds: float
    transcript: Path
    tool_calls: list[str] = field(default_factory=list)


class Agent:
    """An agent conversation, about a board panel or the project.

    The agent may call only the tools it is offered, and may not write board files: the
    user owns the layout.

    Args:
        project: The project.
        board: Board name, or None (then the board tools fail).
        panel: Panel id the prompt is about, or None. A recipe panel gets its recipe's context;
            a static-file panel gets its fit, the script that drew it and ``use_recipe``.
        renders: Render path shared with the board (``State.renders`` in the server).
        llm: Model client (anything with ``profile`` and ``chat``).
        size: Target (w, h) in mm; defaults to the panel's plot area.
        old_size: Size the recipe was designed for; defaults to the plot area.
        max_steps: Cap on model calls per ``run``.
        on_event: Called with each transcript record, and ``tool_start`` records, for progress.
        tools: Tool schemas offered to the model; default ``STATIC_TOOLS`` for a panel without a
            recipe, else ``TOOLS``.
        guard: Called around each file write.
        extra: More context sections for the first message, as markdown.
    """

    def __init__(self, project: Project, board: Optional[str], panel: Optional[str], renders: Renders, llm: Any,
                 size: Optional[tuple[float, float]] = None, old_size: Optional[tuple[float, float]] = None,
                 max_steps: int = MAX_STEPS, on_event: Optional[Callable[[dict], None]] = None,
                 tools: Optional[list[dict]] = None, guard: Optional[Guard] = None,
                 extra: Optional[list[str]] = None):
        self.project, self.board_name, self.panel_id = project, board, panel
        self.pack = styles.for_project(project)
        self.renders, self.llm, self.max_steps, self.on_event = renders, llm, max_steps, on_event
        self.cell_size = self.panel_size(panel) if panel else None
        self.size = tuple(size) if size else self.cell_size
        self.old_size = tuple(old_size) if old_size else self.cell_size
        default = STATIC_TOOLS if panel and not self.has_recipe(panel) else TOOLS
        self.tools, self.guard, self.extra = tools or default, guard, extra or []
        self.offered = {t["function"]["name"] for t in self.tools}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.transcript = project.root / LOG_DIR / f"{stamp}-{panel or 'project'}.jsonl"
        self.last_render: Optional[RenderResult] = None
        self.messages: list[dict] = []

    # -- board and recipe ---------------------------------------------------

    def board(self) -> Board:
        if not self.board_name:
            raise ToolError("no board is open in this session")
        return Board.load(self.project.board_path(self.board_name), self.pack)

    def panel_size(self, pid: str) -> tuple[float, float]:
        b = self.board()
        _, _, w, h = b.content_rect(b.panel(pid))
        return round(w, 3), round(h, 3)

    def has_recipe(self, pid: str) -> bool:
        return "recipe" in (self.board().panel(pid).get("source") or {})

    def recipe(self, pid: str) -> tuple[str, dict, str]:
        """(recipe ``module:function``, params, root-relative module file) of a panel."""
        src = self.board().panel(pid).get("source") or {}
        if "recipe" not in src:
            raise BoardError(f"panel {pid!r} has no recipe source")
        recipe, params = str(src["recipe"]), dict(src.get("params") or {})
        mod = recipe.partition(":")[0].replace(".", "/")
        rel = f"{mod}.py" if (self.project.root / f"{mod}.py").is_file() else f"{mod}/__init__.py"
        return recipe, params, rel

    async def render(self, pid: str, w: Optional[float] = None, h: Optional[float] = None) -> tuple[RenderResult, float, float]:
        """Renders a panel at (w, h), defaulting to the target size (or the cell size of other panels)."""
        recipe, params, _ = self.recipe(pid)
        dw, dh = self.size if pid == self.panel_id else self.panel_size(pid)
        w, h = float(w or dw), float(h or dh)
        full = request_for(self.board(), self.board().panel(pid))
        res = await self.renders.render(RenderRequest(recipe, params, w, h, full.multiples if full else None,
                                                      *((full.preset, full.rc) if full else ())))
        if pid == self.panel_id:
            self.last_render = res
        return res, w, h

    # -- tools --------------------------------------------------------------

    def _path(self, rel: str, write: bool = False) -> Path:
        try:
            p = self.project.resolve(str(rel).strip().removeprefix("./"))
        except PathError as e:
            raise ToolError(str(e))
        if write and ".git" in PurePosixPath(self.project.relative(p)).parts:
            raise ToolError("refusing to edit inside .git")
        if write and p.is_relative_to(self.project.boards_dir):
            raise ToolError(f"{rel} is under boards/, which holds the layout; the user owns the layout, "
                            "so no agent tool edits it. Change the plot's code instead")
        return p

    def t_read_file(self, path: str) -> str:
        p = self._path(path)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        text = p.read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= MAX_READ else text[:MAX_READ] + f"\n[... truncated at {MAX_READ} chars]"

    def t_list_dir(self, path: str = "") -> str:
        self._path(path)
        try:
            entries = self.project.list_dir(str(path).strip().removeprefix("./"))
        except PathError as e:
            raise ToolError(str(e))
        return "\n".join(e["path"] + ("/" if e["dir"] else f"  ({e['size']} B)") for e in entries) or "(empty)"

    def t_grep(self, pattern: str, path: str = "") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"bad regex: {e}")
        base = self._path(path)
        files = [base] if base.is_file() else sorted(
            f for f in base.rglob("*") if f.is_file()
            and not any(q.startswith(".") or q in SKIP_DIRS for q in f.relative_to(base).parts))
        hits = []
        for f in files:
            if f.stat().st_size > 1_000_000:
                continue
            try:
                lines = f.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for i, ln in enumerate(lines, 1):
                if rx.search(ln):
                    hits.append(f"{self.project.relative(f)}:{i}: {ln[:200]}")
                    if len(hits) >= MAX_GREP:
                        return "\n".join(hits + [f"[stopped at {MAX_GREP} matches]"])
        return "\n".join(hits) or "no matches"

    def _write(self, p: Path, text: str) -> None:
        """Writes a project file, with the guard's before and after calls."""
        rel = self.project.relative(p)
        if self.guard:
            self.guard.before(rel)
        p.write_text(text, encoding="utf-8")
        if self.guard:
            self.guard.after(rel)

    def t_write_file(self, path: str, content: str) -> str:
        p = self._path(path, write=True)
        if p.exists():
            raise ToolError(f"{path} exists; use edit_file to change it")
        p.parent.mkdir(parents=True, exist_ok=True)
        self._write(p, content)
        return f"ok: wrote {path}"

    async def t_render_recipe(self, recipe: str, w: float, h: float,
                              params: Optional[dict] = None) -> tuple[str, Optional[bytes]]:
        if locate(self.project, recipe) is None:
            raise ToolError(f"recipe {recipe!r} not found (module:function, importable from the project root)")
        res = await self.renders.render(RenderRequest(recipe, dict(params or {}), float(w), float(h)))
        png = rasterize(self.project.root, res.svg, pack=self.pack) if res.ok and self.llm.profile.vision else None
        return report(res, float(w), float(h), self.pack), png

    def t_edit_file(self, path: str, old_string: str, new_string: str) -> str:
        p = self._path(path, write=True)
        if not p.is_file():
            raise ToolError(f"not a file: {path}")
        if not old_string:
            raise ToolError("old_string is empty")
        text = p.read_text(encoding="utf-8")
        n = text.count(old_string)
        if n > 1:
            at, i = [], text.find(old_string)
            while i >= 0 and len(at) < 10:
                at.append(str(text.count("\n", 0, i) + 1))
                i = text.find(old_string, i + 1)
            raise ToolError(f"old_string matches {n} times in {path} (starting at lines {', '.join(at)}); "
                            "it must match exactly once: include more surrounding lines")
        if n == 0:
            raise ToolError(f"old_string matches 0 times in {path}. " + edit_miss(text, old_string))
        self._write(p, text.replace(old_string, new_string, 1))
        return f"ok: edited {path}"

    async def t_render_panel(self, id: str, w: Optional[float] = None, h: Optional[float] = None) -> tuple[str, Optional[bytes]]:
        try:
            src = self.board().panel(id).get("source") or {}
        except KeyError:
            raise ToolError(f"no panel {id!r}")
        if "file" in src:
            return self.static_report(id)
        try:
            res, w, h = await self.render(id, w, h)
        except BoardError as e:
            raise ToolError(str(e))
        png = rasterize(self.project.root, res.svg, pack=self.pack) if res.ok and self.llm.profile.vision else None
        return report(res, w, h, self.pack), png

    def static_report(self, pid: str) -> tuple[str, Optional[bytes]]:
        """A static-file panel as the board draws it: its fit as text, and a PNG for vision models."""
        b = self.board()
        p = b.panel(pid)
        rel = str(p["source"]["file"])
        _, _, w, h = b.content_rect(p)
        f = fits.panel_fit(self.project, b, p)
        if f is None:
            return f"static file {rel} does not load", None
        fill = f["fill"]
        text = (f"static file {rel}, {fmt_size(*f['natural_mm'])}; the board fits it into the {fmt_size(w, h)} "
                f"plot area with its aspect ratio kept, so it is drawn at {fmt_size(*f['drawn_mm'])}, filling "
                f"{fill[0]:.0%} of the width and {fill[1]:.0%} of the height. It does not re-render when the "
                "cell changes.")
        png = fits.placed_png(self.project, rel, (w, h)) if self.llm.profile.vision else None
        if png:
            text += " The image shows the plot area; its edge is dashed."
        return text, png

    def t_get_board(self) -> str:
        return json.dumps(board_summary(self.board(), self.project), default=str)

    async def t_use_recipe(self, recipe: str, params: Optional[dict] = None) -> str:
        if not self.panel_id:
            raise ToolError("this session is not about a panel")
        if self.has_recipe(self.panel_id):
            raise ToolError(f"panel {self.panel_id!r} already shows a recipe; edit its code instead")
        if locate(self.project, recipe) is None:
            raise ToolError(f"recipe {recipe!r} not found (module:function, importable from the project root)")
        w, h = self.panel_size(self.panel_id)
        res = await self.renders.render(RenderRequest(recipe, dict(params or {}), w, h))
        if not res.ok:
            raise ToolError(f"use_recipe refused: the recipe fails at the plot area.\n{report(res, w, h, self.pack)}")
        b = self.board()
        try:
            apply_ops(self.project, b, [{"op": "set_recipe", "id": self.panel_id, "recipe": recipe,
                                         "params": dict(params or {})}])
        except (BoardError, KeyError, TypeError, ValueError) as e:
            raise ToolError(f"use_recipe refused: {e}")
        self._write(self.project.board_path(self.board_name), b.dumps())
        self.size = self.old_size = self.cell_size = (w, h)
        return (f"ok: panel {self.panel_id} now shows {recipe} at {fmt_size(w, h)}, in the same cell. "
                "Edit the recipe and check it with render_panel.\n" + report(res, w, h, self.pack))

    def t_set_cell(self, id: str, cell: list) -> str:
        b = self.board()
        try:
            warnings = apply_ops(self.project, b, [{"op": "set_cell", "id": id, "cell": cell}])
        except (BoardError, PathError, KeyError, TypeError, ValueError) as e:
            raise ToolError(f"set_cell refused: {e}")
        self._write(self.project.board_path(self.board_name), b.dumps())
        if id == self.panel_id:
            self.size = self.panel_size(id)
        w, h = self.panel_size(id)
        return f"ok: {id} now at cell {list(cell)}, {fmt_size(w, h)}" + "".join(f"\nwarning: {x}" for x in warnings)

    async def call(self, name: str, args: dict) -> tuple[str, Optional[bytes]]:
        """Runs one tool; errors come back as text."""
        fn = getattr(self, f"t_{name}", None)
        if fn is None or name not in self.offered:
            return f"error: unknown tool {name!r}; the tools are {', '.join(sorted(self.offered))}", None
        try:
            out = fn(**args)
            if hasattr(out, "__await__"):
                out = await out
        except ToolError as e:
            return f"error: {e}", None
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}", None
        return out if isinstance(out, tuple) else (out, None)

    # -- loop ---------------------------------------------------------------

    def _log(self, rec: dict) -> None:
        rec = {"t": round(time.time(), 3), **rec}
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        if self.on_event:
            self.on_event(rec)

    def _system(self) -> str:
        system = SYSTEM + "\n" + styles.rules_text(self.pack, self.board().style if self.board_name else None)
        if not self.llm.profile.tools:
            system += "\n" + text_protocol(self.tools)
        return system

    def panel_section(self) -> str:
        """The bound panel's place on the board: cell, letter band and plot area."""
        b = self.board()
        p = b.panel(self.panel_id)
        pg = b.page
        x, y, w, h = pg.rect(p["cell"])
        band = b.bands()[p["id"]]
        letter = b.letters()[p["id"]]
        return (f"## Panel\n\nBoard {self.board_name!r}, panel {self.panel_id!r}"
                + (f" (letter {letter})" if letter else " (no letter)")
                + f", cell {list(p['cell'])} on the {pg.nx} x {pg.ny} grid.\n"
                f"- Cell: {fmt_size(w, h)} at ({x:.4g}, {y:.4g}) mm on the {fmt_size(pg.width, pg.height)} page. "
                "The user sets it; it stays as it is.\n"
                + (f"- Letter band: {band:g} mm at the top of the cell.\n" if band else "")
                + f"- Plot area{', below the band' if band else ''}: {fmt_size(w, h - band)}. The plot must fill it.")

    def static_sections(self, text: str) -> list[str]:
        """What a session about a static-file panel needs: the file's fit (``text``, from
        ``static_report``), the script that drew it and the way to a recipe."""
        b = self.board()
        rel = str(b.panel(self.panel_id)["source"]["file"])
        f = fits.panel_fit(self.project, b, b.panel(self.panel_id))
        bad = fits.problem(f)
        out = [f"## Source\n\n{text[0].upper() + text[1:]}" + (f"\n\nProblem: {bad}." if bad else "")]
        scripts = scripts_for(self.project, rel)
        if not scripts:
            return out + [NO_SCRIPT.format(file=rel)]
        name = new_recipe(self.project, rel)
        shared = shared_helpers(self.project) if name["module_file"].startswith("recipes/") else None
        helpers = f" Other recipes here share `{shared}`; use it too." if shared else ""
        out.append("## How to change it\n\n" + STATIC.format(file=rel, script=scripts[0], size=fmt_size(*self.size),
                                                              helpers=helpers, **name)
                   + (f"\n\nOther files that name {rel}: {', '.join(scripts[1:])}." if scripts[1:] else ""))
        code = (self.project.root / scripts[0]).read_text(encoding="utf-8", errors="replace")
        if len(code) > MAX_SCRIPT:
            code = code[:MAX_SCRIPT] + f"\n[... truncated at {MAX_SCRIPT} chars; read_file has the rest]"
        return out + [f"## Script ({scripts[0]})\n\n```python\n{code}\n```"]

    async def context(self, prompt: str) -> list[dict]:
        """System and first user message.

        With a recipe panel: the task, the panel's cell and plot area, recipe source, sizes,
        lint and render. With a static-file panel: the task, the cell and plot area, the file's
        fit, the script that drew it, and the file as placed. Without a panel: the task and
        the board (if any). Then the extra sections.
        """
        if not self.panel_id:
            parts = [f"## Task\n\n{prompt}"]
            if self.board_name:
                parts.append(f"## Board {self.board_name!r}\n\n{self.t_get_board()}")
            text = "\n\n".join(parts + self.extra)
            return [{"role": "system", "content": self._system()},
                    {"role": "user", "content": [{"type": "text", "text": text}]}]
        if not self.has_recipe(self.panel_id):
            src = self.board().panel(self.panel_id).get("source") or {}
            fit_text, png = self.static_report(self.panel_id) if "file" in src else ("", None)
            body = self.static_sections(fit_text) if "file" in src else [
                "## Source\n\nThe panel has no source yet. To draw it, write a recipe module with write_file, "
                "check it with render_recipe at the plot area, and show it with use_recipe."]
            text = "\n\n".join([f"## Task\n\n{prompt}", self.panel_section(), *body, *self.extra])
            content: list[dict] = [{"type": "text", "text": text}]
            if png:
                content.append(image_part(png))
            return [{"role": "system", "content": self._system()}, {"role": "user", "content": content}]
        recipe, params, rel = self.recipe(self.panel_id)
        res, w, h = await self.render(self.panel_id)
        system = self._system()
        text = "\n\n".join([
            f"## Task\n\n{prompt}",
            self.panel_section(),
            f"Recipe `{recipe}`" + (f" with params {json.dumps(params)}" if params else "")
            + f". Old size: {fmt_size(*self.old_size)}. New size: {fmt_size(*self.size)}.",
            f"## Recipe source ({rel})\n\n```python\n{self.t_read_file(rel)}\n```",
            f"## Current render at the new size\n\n{report(res, w, h, self.pack)}",
            *self.extra,
        ])
        fonts = styles.font_problems(self.pack, (res.summary or {}).get("fonts_found") if res.ok else None)
        if fonts:
            text += "\n\n## Style pack warnings\n\n" + "\n".join(f"- {f}" for f in fonts)
        content: list[dict] = [{"type": "text", "text": text}]
        if res.ok and self.llm.profile.vision:
            content.append(image_part(rasterize(self.project.root, res.svg, pack=self.pack)))
        return [{"role": "system", "content": system}, {"role": "user", "content": content}]

    async def run(self, prompt: str) -> Result:
        """Runs the loop until the model stops calling tools or hits the step cap."""
        t0 = time.perf_counter()
        prof = self.llm.profile
        self._log({"type": "start", "profile": prof.name, "model": prof.model, "tools": prof.tools,
                   "vision": prof.vision, "board": self.board_name, "panel": self.panel_id,
                   "size": self.size, "old_size": self.old_size, "prompt": prompt})
        new = [{"role": "user", "content": prompt}] if self.messages else await self.context(prompt)
        messages = self.messages
        for m in new:
            messages.append(m)
            self._log({"type": "message", "message": _redact(m)})
        usage = {"prompt": 0, "completion": 0, "total": 0}
        try:
            return await self._loop(messages, usage, t0)
        except asyncio.CancelledError:
            self._close_calls(messages)
            self._log({"type": "end", "stopped": "aborted", "usage": usage,
                       "seconds": round(time.perf_counter() - t0, 2)})
            raise

    def _close_calls(self, messages: list[dict]) -> None:
        """Answers the last reply's unanswered tool calls, so the history stays valid after an abort."""
        last = next((i for i in range(len(messages) - 1, -1, -1) if messages[i]["role"] == "assistant"), None)
        calls = messages[last].get("tool_calls") or [] if last is not None else []
        if calls:
            done = {m.get("tool_call_id"): m for m in messages[last + 1:] if m["role"] == "tool"}
            del messages[last + 1:]
            for c in calls:
                messages.append(done.get(c["id"]) or {"role": "tool", "tool_call_id": c["id"],
                                                      "content": "error: aborted by the user"})
        messages.append({"role": "user", "content": "[The user aborted this turn.]"})

    async def _loop(self, messages: list[dict], usage: dict, t0: float) -> Result:
        prof = self.llm.profile
        called: list[str] = []
        steps, stopped, text, nudged = 0, "step_cap", "", False
        while steps < self.max_steps:
            steps += 1
            reply = await self.llm.chat(prune_images(messages), self.tools)
            for k, v in reply.usage.items():
                usage[k] = usage.get(k, 0) + (v or 0)
            messages.append(reply.message)
            text = reply.content
            self._log({"type": "reply", "step": steps, "seconds": round(reply.seconds, 2), "usage": reply.usage,
                       "reasoning": reply.reasoning, "content": reply.content,
                       "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments, "error": c.error}
                                      for c in reply.tool_calls]})
            if not reply.tool_calls:
                if prof.tools or nudged or {"edit_file", "write_file", "set_cell", "use_recipe"} & set(called):
                    stopped = "done"
                    break
                nudged = True  # text mode: a reply that announces a call but writes none
                m = {"role": "user", "content": NUDGE}
                messages.append(m)
                self._log({"type": "message", "message": m})
                continue
            results, images = [], []
            for c in reply.tool_calls:
                t1 = time.perf_counter()
                if self.on_event:
                    self.on_event({"type": "tool_start", "step": steps, "id": c.id, "name": c.name})
                out, png = (f"error: {c.error}", None) if c.error else await self.call(c.name, c.arguments)
                called.append(c.name)
                self._log({"type": "tool", "step": steps, "id": c.id, "name": c.name, "arguments": c.arguments,
                           "seconds": round(time.perf_counter() - t1, 2), "result": out, "image": png is not None})
                results.append((c, out))
                if png:
                    images.append(image_part(png))
            if prof.tools:
                new = [{"role": "tool", "tool_call_id": c.id, "content": out} for c, out in results]
                if images:
                    new.append({"role": "user", "content": [
                        {"type": "text", "text": "Images of the renders above, in order."}, *images]})
            else:
                body = "\n\n".join(f"### Result of {c.name}({json.dumps(c.arguments)})\n\n{out}" for c, out in results)
                new = [{"role": "user", "content": [{"type": "text", "text": "Tool results:\n\n" + body}, *images]}]
            for m in new:
                messages.append(m)
                self._log({"type": "message", "message": _redact(m)})
        seconds = time.perf_counter() - t0
        self._log({"type": "end", "stopped": stopped, "steps": steps, "usage": usage, "seconds": round(seconds, 2)})
        return Result(text=text, stopped=stopped, steps=steps, usage=usage, seconds=seconds,
                      transcript=self.transcript, tool_calls=called)


def last_size(project: Project, recipe: str, params: dict) -> Optional[tuple[float, float]]:
    """Size of the most recent render of a recipe call, from its meta.json."""
    meta = project.root / Path(output_path(recipe, params, 1, 1)).parent / "meta.json"
    if not meta.is_file():
        return None
    m = json.loads(meta.read_text(encoding="utf-8"))
    return float(m["width_mm"]), float(m["height_mm"])


def adapt_prompt(panel: str, old: tuple[float, float], new: tuple[float, float]) -> str:
    """The "Adapt to size" prompt."""
    return ADAPT.format(id=panel, old=fmt_size(*old), new=fmt_size(*new))
