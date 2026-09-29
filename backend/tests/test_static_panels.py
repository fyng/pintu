import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from pintu import agent as ag, fit as fits
from pintu.board import Board
from pintu.catalog import scripts_for
from pintu.llm import LLM, Profile
from pintu.project import Project
from pintu.recipes import SubprocessRunner
from pintu.renders import Renders
from pintu.server import create_app
from pintu.sessions import SessionManager, promote_source

from test_agent import FakeClient, native

RECIPE = ("from matplotlib.figure import Figure\n\n\n"
          "def lines(w, h):\n"
          "    fig = Figure(figsize=(w / 25.4, h / 25.4))\n"
          "    fig.add_axes((0.2, 0.2, 0.75, 0.75)).plot([0, 1], [0, 1])\n"
          "    return fig\n")


@pytest.fixture
def project(demo):
    """The demo, with panel lines narrowed to half its width: its 89 x 55 mm file fills half the height."""
    p = Project.open(demo)
    path = p.board_path("demo")
    path.write_text(path.read_text().replace("cell: [0, 0, 18, 18]", "cell: [0, 0, 9, 18]"))
    return p


def agent(project, replies=(), vision=False, panel="lines", **kw):
    client = FakeClient(list(replies))
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=True, vision=vision), client=client)
    renders = Renders(project, SubprocessRunner(project), ag._no_board)
    return ag.Agent(project, "demo", panel, renders, llm, **kw), client


def test_natural_size_contain_and_problem(project):
    assert fits.natural_size(project, "plots/lines.pdf") == (89.0, 55.0)
    assert fits.natural_size(project, "plots/heatmap.png") == pytest.approx((120.0, 55.0), abs=0.1)
    assert fits.natural_size(project, "plots/nope.pdf") is None
    f = fits.contain((89, 55), (43.5, 55))
    assert f["drawn_mm"] == [43.5, 26.88] and f["fill"] == [1.0, 0.489]
    assert fits.problem({"file": "x", **f}) == "51% of the plot area's height is empty"
    assert fits.problem({"file": "x", **fits.contain((89, 55), (90, 55))}) is None
    png = fits.placed_png(project, "plots/lines.pdf", (43.5, 55))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_scripts_for(project):
    assert scripts_for(project, "plots/lines.pdf") == ["scripts/make_plots.py"]
    assert scripts_for(project, "plots/nope.pdf") == []
    (project.root / "plots/lines.pdf.meta.json").write_text(json.dumps({"script": "scripts/make_gallery.py"}))
    assert scripts_for(project, "plots/lines.pdf") == ["scripts/make_gallery.py"]


def test_promote_source_without_sidecar(project):
    info = promote_source(project, "plots/lines.pdf")
    assert info["script"] == "scripts/make_plots.py" and info["size"] == [89.0, 55.0]
    assert info["fields"]["recipe"] == "recipes.lines:lines"


def test_board_summary_fit(project):
    board = ag.board_summary(Board.load(project.board_path("demo")), project)
    lines = board["panels"][0]
    assert lines["plot_mm"] == [43.5, 55.0] and lines["cell_mm"] == [0.0, 0.0, 43.5, 58.5]
    assert lines["fit"] == {"natural_mm": [89.0, 55.0], "drawn_mm": [43.5, 26.88], "fill": [1.0, 0.489]}
    assert "fit" not in ag.board_summary(Board.load(project.board_path("demo")))["panels"][0]


def test_static_panel_context(project):
    a, _ = agent(project, vision=True)
    assert a.size == (43.5, 55.0) and {"use_recipe", "write_file", "render_recipe"} <= a.offered
    assert "set_cell" not in a.offered
    msgs = asyncio.run(a.context("resize plot to use full vertical space"))
    assert "user owns the layout" in msgs[0]["content"]
    body = msgs[1]["content"][0]["text"]
    assert "panel 'lines' (letter a), cell [0, 0, 9, 18]" in body
    assert "Cell: 43.5 x 58.5 mm" in body and "Letter band: 3.5 mm" in body
    assert "Plot area, below the band: 43.5 x 55 mm. The plot must fill it." in body
    assert "drawn at 43.5 x 26.88 mm" in body and "51% of the plot area's height is empty" in body
    assert "recipes/lines.py" in body and "def lines(w, h)" in body and "use_recipe" in body
    assert "from pintu_sdk import panel" in body and "share `from .common import axes_mm, figure, RC`" in body
    assert "## Script (scripts/make_plots.py)" in body and 'f.savefig(OUT / "lines.pdf")' in body
    png = base64.b64decode(msgs[1]["content"][1]["image_url"]["url"].split(",", 1)[1])
    assert png[:4] == b"\x89PNG"


def test_static_panel_without_script(project):
    (project.root / "scripts/make_plots.py").unlink()
    a, _ = agent(project)
    body = asyncio.run(a.context("taller"))[1]["content"][0]["text"]
    assert "No sidecar names the script" in body and "## Script" not in body


def test_render_panel_static(project):
    a, _ = agent(project, vision=True)
    out, png = asyncio.run(a.call("render_panel", {"id": "lines"}))
    assert "drawn at 43.5 x 26.88 mm" in out and "49% of the height" in out and png[:4] == b"\x89PNG"


def test_use_recipe(project):
    a, _ = agent(project)
    before = Board.load(project.board_path("demo"))
    out, _ = asyncio.run(a.call("use_recipe", {"recipe": "recipes.lines:lines"}))
    assert "not found" in out
    (project.root / "recipes/lines.py").write_text("def lines(w, h):\n    raise ValueError('boom')\n")
    out, _ = asyncio.run(a.call("use_recipe", {"recipe": "recipes.lines:lines"}))
    assert "fails at the plot area" in out and "boom" in out
    assert Board.load(project.board_path("demo")).panel("lines")["source"] == {"file": "plots/lines.pdf"}
    (project.root / "recipes/lines.py").write_text(RECIPE)
    out, _ = asyncio.run(a.call("use_recipe", {"recipe": "recipes.lines:lines"}))
    assert out.startswith("ok: panel lines now shows recipes.lines:lines at 43.5 x 55 mm")
    after = Board.load(project.board_path("demo"))
    assert after.panel("lines")["source"] == {"recipe": "recipes.lines:lines"}
    assert [p["cell"] for p in after.panels] == [p["cell"] for p in before.panels]
    out, _ = asyncio.run(a.call("render_panel", {"id": "lines"}))
    assert "render ok at 43.5 x 55 mm" in out
    out, _ = asyncio.run(a.call("use_recipe", {"recipe": "recipes.lines:lines"}))
    assert "already shows a recipe" in out


def test_session_on_static_panel(project):
    client = FakeClient([native(calls=[("write_file", {"path": "recipes/lines.py", "content": RECIPE})]),
                         native(calls=[("use_recipe", {"recipe": "recipes.lines:lines"})]),
                         native("Made it a recipe that fills the plot area.")])
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=True), client=client)
    mgr = SessionManager(project, Renders(project, SubprocessRunner(project), ag._no_board), lambda: llm)

    async def go():
        s = await mgr.create("prompt", "demo", "lines", "resize plot to use full vertical space")
        return await mgr.wait(s["id"])

    s = asyncio.run(go())
    a = mgr.agents[s["id"]]
    assert a.panel_id == "lines" and a.max_steps == ag.MAX_STEPS + ag.RECIPE_STEPS
    tools = [t["function"]["name"] for t in client.requests[0]["tools"]]
    assert "use_recipe" in tools and "set_cell" not in tools
    assert "## Source" in client.requests[0]["messages"][1]["content"][0]["text"]
    assert s["result"]["stopped"] == "done"
    files = {f["path"] for f in mgr.diff(s["id"])["files"]}
    assert files == {"recipes/lines.py", "boards/demo.board.yaml"}
    mgr.revert(s["id"])
    assert Board.load(project.board_path("demo")).panel("lines")["source"] == {"file": "plots/lines.pdf"}


def test_board_view_fit(project):
    with TestClient(create_app(project, watch=False)) as c:
        panels = c.get("/api/boards/demo").json()["panels"]
    fit = panels[0]["fit"]
    assert fit["drawn"] == [43.5, 26.88] and fit["problem"] == "51% of the plot area's height is empty"
    assert panels[1]["fit"]["problem"] is None
