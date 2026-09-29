import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from pintu.project import Project
from pintu.recipes import RenderRequest, SubprocessRunner, locate
from pintu.renders import Renders
from pintu.server import create_app

RECIPE = '''from matplotlib.figure import Figure


def plot(w, h, label="x"):
    fig = Figure(figsize=(w / 25.4, h / 25.4))
    fig.add_subplot().set_xlabel(label)
    return fig


class Plots:
    def wide(w, h):
        return plot(w, h, "wide")
'''

BOARD = """version: 1
page: {width: 180, height: 90, grid: [36, 36], gutter: 3, style: nature}
panels:
  - id: r
    cell: [0, 0, 18, 18]
    source: {recipe: rec:plot, params: {label: hi}}
  - id: s
    cell: [18, 0, 36, 18]
"""


class Counting(SubprocessRunner):
    def __init__(self, project):
        super().__init__(project)
        self.calls = []

    async def render(self, req):
        self.calls.append(req)
        return await super().render(req)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "rec.py").write_text(RECIPE)
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards/b.board.yaml").write_text(BOARD)
    return Project.open(tmp_path)


def test_cache_hit_and_miss(project, tmp_path):
    async def main():
        runner = Counting(project)
        rs = Renders(project, runner, on_done=None)
        req = RenderRequest("rec:plot", {"label": "a"}, 60, 40)
        a = await rs.render(req)
        b = await rs.render(req)
        assert a.ok and not a.cached and b.cached and b.svg == a.svg and len(runner.calls) == 1
        await rs.render(RenderRequest("rec:plot", {"label": "b"}, 60, 40))
        await rs.render(RenderRequest("rec:plot", {"label": "a"}, 61, 40))
        assert len(runner.calls) == 3
        (tmp_path / "rec.py").write_text(RECIPE + "\n# edited\n")
        assert not (await rs.render(req)).cached and len(runner.calls) == 4
        (tmp_path / "rec.py").write_text(RECIPE)  # back to the first code: its SVG was overwritten
        assert not (await rs.render(req)).cached
        rs.cache.save()
        again = Renders(project, runner, on_done=None)
        assert (await again.render(req)).cached  # index persisted under .pintu/
    asyncio.run(main())


def test_locate(project, tmp_path):
    assert locate(project, "rec:plot") == (tmp_path / "rec.py", 4)
    assert locate(project, "rec:Plots.wide") == (tmp_path / "rec.py", 11)
    assert locate(project, "rec:nope") is None
    assert locate(project, "nomod:plot") is None


def wait_for(client, pred, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        b = client.get("/api/boards/b").json()
        if pred(b):
            return b
        time.sleep(0.1)
    raise AssertionError(f"timed out; last board {b}")


def panel(b, pid="r"):
    return next(p for p in b["panels"] if p["id"] == pid)


def test_board_resize_watch_missing_and_open(project, tmp_path, monkeypatch):
    runner = Counting(project)
    with TestClient(create_app(project, runner=runner)) as client:
        b = wait_for(client, lambda b: panel(b)["render"]["status"] == "ok")
        p = panel(b)
        assert p["file"].endswith("/88.5x40.svg") and p["params"] == {"label": "hi"}
        assert client.get("/api/boards/b/preview.svg").status_code == 200

        # The recipe renders below the 3.5 mm letter band.
        # Board -> plot: resize renders at the new size; the old render shows meanwhile.
        r = client.post("/api/boards/b/ops", json={"ops": [{"op": "set_cell", "id": "r", "cell": [0, 0, 12, 18]}]})
        assert panel(r.json())["file"] == p["file"]
        b = wait_for(client, lambda b: panel(b)["file"].endswith("/58x40.svg"))
        # Back to the first size: a cache hit, no render.
        n = len(runner.calls)
        r = client.post("/api/boards/b/ops", json={"ops": [{"op": "set_cell", "id": "r", "cell": [0, 0, 18, 18]}]})
        assert panel(r.json())["file"] == p["file"] and panel(r.json())["render"]["cached"]
        assert len(runner.calls) == n

        # Param edit and a new recipe on an empty panel.
        client.post("/api/boards/b/ops", json={"ops": [{"op": "set_recipe", "id": "s", "recipe": "rec:Plots.wide"},
                                                       {"op": "set_recipe", "id": "r", "recipe": "rec:plot", "params": {"label": "yo"}}]})
        b = wait_for(client, lambda b: panel(b, "s")["render"]["status"] == "ok" and panel(b)["file"] != p["file"])
        client.app.state.pintu.writer.submit(lambda: None).result()
        assert "params: {label: yo}" in (tmp_path / "boards/b.board.yaml").read_text()

        # Code -> plot: saving the recipe re-renders its panels.
        v = panel(b)["fileVersion"]
        n = len(runner.calls)
        time.sleep(0.05)
        (tmp_path / "rec.py").write_text(RECIPE.replace('"wide"', '"wider"'))
        b = wait_for(client, lambda b: panel(b)["fileVersion"] != v and panel(b, "s")["render"]["status"] == "ok")
        assert len(runner.calls) == n + 2

        # Code -> board: a removed recipe keeps its last render and shows missing.
        (tmp_path / "rec.py").write_text(RECIPE.replace("class Plots", "class Other"))
        b = wait_for(client, lambda b: panel(b, "s")["render"]["status"] == "missing" and panel(b)["render"]["status"] == "ok")
        assert panel(b, "s")["file"] and panel(b)["render"]["status"] == "ok"

        # Recipe error: the last render stays; the error is reported.
        (tmp_path / "rec.py").write_text(RECIPE.replace("    return fig\n\n\nclass", "    return 1 / 0\n\n\nclass"))
        b = wait_for(client, lambda b: panel(b)["render"]["status"] == "error")
        assert "ZeroDivisionError" in panel(b)["render"]["error"] and panel(b)["file"]

        # Plot -> code.
        loc = client.get("/api/recipes/locate", params={"recipe": "rec:plot"}).json()
        assert loc["file"] == "rec.py" and loc["line"] == 4 and "def plot" in loc["text"]
        assert client.get("/api/recipes/locate", params={"recipe": "rec:nope"}).status_code == 404
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setenv("EDITOR", "true")
        monkeypatch.delenv("VISUAL", raising=False)
        o = client.post("/api/recipes/open", json={"recipe": "rec:plot"}).json()
        assert o == {"opened": True, "command": f"true +4 {tmp_path / 'rec.py'}"}


def test_sync_queues_and_rerenders_deleted_output(project, tmp_path):
    from pintu.board import Board

    async def main():
        done = asyncio.Event()

        async def on_done(name):
            done.set()

        rs = Renders(project, Counting(project), on_done)
        rs.start()
        try:
            board = Board.load(tmp_path / "boards/b.board.yaml")
            assert rs.sync("b", board) and rs.get("b", "r").status == "rendering"
            assert not rs.sync("b", board)  # unchanged: nothing queued twice
            await asyncio.wait_for(done.wait(), 60)
            st = rs.get("b", "r")
            assert st.status == "ok" and rs.shown("b", {"id": "r"}) == st.svg
            (tmp_path / st.svg).unlink()
            done.clear()
            assert rs.sync("b", board) and st.status == "rendering"
            await asyncio.wait_for(done.wait(), 60)
            assert (tmp_path / st.svg).is_file()
        finally:
            await rs.stop()
    asyncio.run(main())


def test_request_for_uses_size_below_band():
    from pintu.board import Board
    from pintu.renders import request_for
    b = Board.loads("""\
page: {width: 183, height: 173, grid: 36, gutter: 3}
panels:
  - {id: a, cell: [0, 0, 18, 18], source: {recipe: "m:f"}}
  - {id: b, cell: [18, 0, 36, 18], letter: false, source: {recipe: "m:f"}}
  - {id: c, cell: [0, 18, 36, 36], source: {file: x.pdf}}
""")
    _, _, w, h = b.page.rect([0, 0, 18, 18])
    a, bb = request_for(b, b.panel("a")), request_for(b, b.panel("b"))
    assert (a.width_mm, a.height_mm) == (round(w, 2), round(h - 3.5, 2))
    assert (bb.width_mm, bb.height_mm) == (round(w, 2), round(h, 2))
    assert request_for(b, b.panel("c")) is None
