import asyncio
import json

from pintu.project import Project
from pintu.recipes import RenderRequest, SubprocessRunner, output_path, param_hash

RECIPE = '''
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

def plot(w, h, n=3):
    fig = Figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_subplot()
    ax.plot(range(n))
    ax.set_xlabel("x")
    return fig

def bad(w, h):
    return 1
'''


def _project(tmp_path):
    (tmp_path / "rec.py").write_text(RECIPE)
    return Project.open(tmp_path, create=True)


def test_output_path_stable():
    assert param_hash({"a": 1, "b": 2}) == param_hash({"b": 2, "a": 1})
    assert output_path("pkg.mod:fn", {}, 89.0, 56.25) == f"pintu_out/pkg.mod.fn/{param_hash({})}/89x56.25.svg"


def test_subprocess_render(tmp_path):
    project = _project(tmp_path)
    res = asyncio.run(SubprocessRunner(project).render(RenderRequest("rec:plot", {"n": 5}, 89, 60)))
    assert res.ok, res.error
    svg = tmp_path / res.svg
    assert svg.exists() and "<svg" in svg.read_text()
    assert res.summary["size_mm"] == [89.0, 60.0]
    assert res.summary["axes"][0]["xlabel"] == "x"
    meta = json.loads((svg.parent / "meta.json").read_text())
    assert meta["params"] == {"n": 5} and meta["width_mm"] == 89 and meta["code_hash"] == res.code_hash


def test_subprocess_render_error(tmp_path):
    project = _project(tmp_path)
    res = asyncio.run(SubprocessRunner(project).render(RenderRequest("rec:bad", {}, 50, 40)))
    assert not res.ok and "expected matplotlib Figure" in res.error
    res = asyncio.run(SubprocessRunner(project).render(RenderRequest("rec:missing", {}, 50, 40)))
    assert not res.ok and "AttributeError" in res.error
