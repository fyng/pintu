import asyncio
import shutil
import time
from pathlib import Path

import pytest
from conftest import KARE_SKIP, kare_dir
from fastapi.testclient import TestClient

from pintu import agent as ag, lint, style
from pintu.board import Board
from pintu.cache import cache_key
from pintu.llm import LLM, Profile
from pintu.project import Project
from pintu.recipes import RenderRequest, SubprocessRunner
from pintu.renders import Renders, request_for
from pintu.server import create_app

REPO = Path(__file__).resolve().parents[2]
STRICT = REPO / "examples" / "stylepacks" / "strict"
KARE = kare_dir()
PLEX = KARE / "core" / "fonts" / "ibm-plex-sans" if KARE else None

RECIPES = '''from matplotlib.figure import Figure

RC = dict(labelsize=5, length=1.42, width=0.5)


def _fig(w, h):
    fig = Figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_axes([0.25, 0.3, 0.7, 0.6])
    ax.plot([0, 1, 2], [1, 3, 2], lw=1)
    ax.set_xlabel("Time (months)", fontsize=6)
    for s in ax.spines.values():
        s.set_linewidth(0.5)
    return fig, ax


def clean(w, h):
    fig, ax = _fig(w, h)
    ax.tick_params(**RC)
    return fig


def small_tick_labels(w, h):
    """4 pt tick labels."""
    fig, ax = _fig(w, h)
    ax.tick_params(**{**RC, "labelsize": 4})
    return fig


def long_ticks(w, h):
    """4 pt long tick marks."""
    fig, ax = _fig(w, h)
    ax.tick_params(**{**RC, "length": 4})
    return fig


def thick(w, h):
    fig, ax = _fig(w, h)
    ax.tick_params(**{**RC, "width": 1.5})
    ax.lines[0].set_linewidth(3)
    for s in ax.spines.values():
        s.set_linewidth(1.5)
    return fig


def overflow(w, h):
    fig, ax = _fig(w, h)
    ax.tick_params(**RC)
    fig.text(0.9, 0.5, "A label that runs off the right edge", fontsize=6)
    return fig


def big_text(w, h):
    fig, ax = _fig(w, h)
    ax.tick_params(**RC)
    ax.set_xlabel("Time", fontsize=9)
    return fig


def wrong_size(w, h):
    fig, ax = _fig(w + 2, h)
    ax.tick_params(**RC)
    return fig


def plex(w, h):
    fig, ax = _fig(w, h)
    ax.tick_params(**RC)
    ax.xaxis.label.set_family("IBM Plex Sans")
    return fig
'''

BOARD = """version: 1
page: {width: 183, height: 60, grid: [36, 36], gutter: 3, style: nature}
panels:
  - id: t
    cell: [0, 0, 18, 36]
    source: {recipe: "rec:small_tick_labels"}
"""


def make(tmp_path, pack=None):
    (tmp_path / "rec.py").write_text(RECIPES)
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards" / "b.board.yaml").write_text(BOARD)
    if pack:
        (tmp_path / "pintu.toml").write_text(f'[style]\npack = "{pack}"\n')
    return Project.open(tmp_path)


def render(project, recipe, w=60, h=40):
    rs = Renders(project, SubprocessRunner(project), on_done=None)
    res = asyncio.run(rs.render(RenderRequest(f"rec:{recipe}", {}, w, h)))
    assert res.ok, res.error
    return res, rs


def rules(res):
    return {i["rule"] for i in res.lint}


def test_exit_strict_pack_flags_4pt_ticks(tmp_path):
    """B2 exit test: design-system rules flag a panel with 4 pt ticks."""
    project = make(tmp_path, STRICT)
    res, _ = render(project, "small_tick_labels")
    assert {"tick-labels", "font"} <= rules(res)
    msg = next(i["message"] for i in res.lint if i["rule"] == "tick-labels")
    assert "is 4 pt (allowed 5-6 pt)" in msg and "tick label" in msg
    res, _ = render(project, "long_ticks")
    assert rules(res) == {"tick-length"}
    assert "is 4 pt (allowed 1-3 pt)" in res.lint[0]["message"]
    assert render(project, "clean")[0].lint == []


def test_exit_through_api(tmp_path):
    """The lint reaches the board API, beside the render status."""
    project = make(tmp_path, STRICT)
    with TestClient(create_app(project, runner=SubprocessRunner(project))) as client:
        t0 = time.time()
        while time.time() - t0 < 60:
            b = client.get("/api/boards/b").json()
            r = b["panels"][0]["render"]
            if r["status"] == "ok":
                break
            time.sleep(0.1)
        assert r["status"] == "ok"
        assert "tick-labels" in {i["rule"] for i in r["lint"]}
        assert b["preset"]["pack"] == "strict"


def test_default_pack_has_no_tick_rules(tmp_path):
    project = make(tmp_path)
    assert rules(render(project, "small_tick_labels")[0]) == {"font"}  # below 5 pt text
    assert render(project, "long_ticks")[0].lint == []
    assert render(project, "thick")[0].lint == []


def test_width_rules(tmp_path):
    res, _ = render(make(tmp_path, STRICT), "thick")
    assert rules(res) == {"tick-width", "axis-lines", "data-lines"}
    assert any("is 3 pt (allowed 0.25-2 pt)" in i["message"] for i in res.lint)


def test_builtin_rules(tmp_path):
    project = make(tmp_path)
    res, _ = render(project, "overflow")
    assert rules(res) == {"overflow"}
    res, _ = render(project, "big_text")
    assert [i["message"] for i in res.lint] == ["text 'Time' is 9 pt (allowed 5-7 pt)"]
    res, _ = render(project, "wrong_size")
    assert rules(res) == {"size"} and "figure is 62 x 40 mm, cell is 60 x 40 mm" in res.lint[0]["message"]


def test_size_tolerance():
    s = {"size_mm": [60.08, 40.0], "axes": [], "texts": []}
    assert lint.check(s, 60, 40) == []
    assert lint.lines(lint.check({**s, "size_mm": [60.2, 40]}, 60, 40))[0].startswith("size: ")


def test_missing_font_in_render():
    s = {"size_mm": [60, 40], "axes": [], "texts": [], "fonts": ["No Such Font 123", "DejaVu Sans"]}
    out = lint.check(s, 60, 40)
    assert len(out) == 1 and out[0]["rule"] == "font" and "'No Such Font 123', which Typst cannot find" in out[0]["message"]


def test_missing_pack_font(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "stylepack.toml").write_text('[pack]\nname = "x"\ndefault_preset = "a"\n[fonts]\nfamily = ["No Such Font 123"]\n'
                                      "[presets.a]\nwidths = {full = 100}\nmax_height = 80\n")
    pack = style.load(d)
    out = style.font_problems(pack, mpl_found=[])
    assert len(out) == 2 and "not found by Typst" in out[0] and "not found by matplotlib" in out[1]
    (tmp_path / "proj").mkdir()
    project = make(tmp_path / "proj", d)
    res, rs = render(project, "clean")
    assert rs.fonts_found == []  # the worker checked the pack's family in the recipe env
    with TestClient(create_app(project, runner=SubprocessRunner(project), watch=False)) as client:
        client.app.state.pintu.renders.fonts_found = []
        warnings = client.get("/api/boards/b").json()["warnings"]
    assert any("not found by Typst" in w for w in warnings) and any("matplotlib" in w for w in warnings)


@pytest.mark.skipif(not (PLEX and PLEX.is_dir()), reason=KARE_SKIP)
def test_pack_font_paths(tmp_path):
    """A pack that ships a font: Typst and the recipe kernel both find it."""
    d = tmp_path / "p"
    shutil.copytree(PLEX, d / "fonts")
    (d / "stylepack.toml").write_text('[pack]\nname = "x"\ndefault_preset = "a"\n'
                                      '[fonts]\nfamily = ["IBM Plex Sans"]\npaths = ["fonts"]\n'
                                      "[presets.a]\nwidths = {full = 100}\nmax_height = 80\n")
    pack = style.load(d)
    assert "ibm plex sans" in style.typst_families(pack.font_paths)
    (tmp_path / "proj").mkdir()
    project = make(tmp_path / "proj", d)
    res, rs = render(project, "plex")
    assert rs.fonts_found == ["IBM Plex Sans"] and "IBM Plex Sans" in res.summary["fonts"]
    assert res.lint == [] and style.font_problems(pack, rs.fonts_found) == []


def test_agent_context_has_pack_rules_and_lint(tmp_path):
    project = make(tmp_path, STRICT)
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=True, vision=False), client=object())
    a = ag.Agent(project, "b", "t", Renders(project, SubprocessRunner(project), ag._no_board), llm)
    msgs = asyncio.run(a.context("Fix the lint"))
    system, body = msgs[0]["content"], msgs[1]["content"][0]["text"]
    assert "Style rules (pack 'strict', preset 'nature')" in system
    assert "Size of drawn tick labels: 5-6 pt" in system
    assert "tick-labels: " in body and "is 4 pt (allowed 5-6 pt)" in body
    out, _ = asyncio.run(a.call("render_panel", {"id": "t"}))
    assert "tick-labels: " in out


RC_PACK = """\
[pack]
name = "rc"
default_preset = "a"

[matplotlib]
rc = {"axes.linewidth" = 0.5, "xtick.labelsize" = 5, "ytick.labelsize" = 5}

[[lint.rules]]
id = "axis-lines"
property = "spine_width_pt"
max = 1

[presets.a]
widths = {full = 183}
max_height = 170

[presets.b]
widths = {full = 183}
max_height = 170
matplotlib = {rc = {"axes.linewidth" = 1.25}}
lint = {rules = [{id = "tick-labels", property = "tick_label_pt", min = 6}]}
"""

RC_PROBE = '''from matplotlib.figure import Figure


def probe(w, h):
    fig = Figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_axes([0.25, 0.3, 0.7, 0.6])
    ax.plot([0, 1], [0, 1], lw=1)
    return fig
'''


def test_pack_rc_and_preset_lint_in_render(tmp_path):
    """The kernel applies the preset's merged rc; lint uses the preset's merged rules."""
    d = tmp_path / "pack"
    d.mkdir()
    (d / "stylepack.toml").write_text(RC_PACK)
    (tmp_path / "proj").mkdir()
    project = make(tmp_path / "proj", d)
    (project.root / "probe.py").write_text(RC_PROBE)
    rs = Renders(project, SubprocessRunner(project), on_done=None)
    out = {}
    for name in ("a", "b"):
        b = Board.loads(f"version: 1\npage: {{width: 183, height: 60, style: {name}}}\npanels:\n"
                        "  - {id: t, cell: [0, 0, 18, 36], source: {recipe: 'probe:probe'}}\n", rs.pack)
        req = request_for(b, b.panel("t"))
        out[name] = req, asyncio.run(rs.render(req))
        assert out[name][1].ok, out[name][1].error
    (ra, a), (rb, b) = out["a"], out["b"]
    assert ra.rc["axes.linewidth"] == 0.5 and rb.rc["axes.linewidth"] == 1.25 and ra.preset == "a"
    assert a.summary["spine_width_pt"] == [0.5] and b.summary["spine_width_pt"] == [1.25]
    assert {t["fontsize_pt"] for t in a.summary["texts"]} == {5}
    assert rules(a) == set() and rules(b) == {"axis-lines", "tick-labels"}
    assert cache_key(ra, "h") != cache_key(rb, "h") and a.svg != b.svg


def test_worker_rc_does_not_leak(tmp_path):
    import matplotlib
    from pintu import worker
    (tmp_path / "probe.py").write_text(RC_PROBE)
    base = dict(root=str(tmp_path), recipe="probe:probe", width_mm=60, height_mm=40)
    before = matplotlib.rcParams["axes.linewidth"]
    res = worker.render({**base, "out": str(tmp_path / "a.svg"), "rc": {"axes.linewidth": 1.5}})
    assert res["ok"] and res["summary"]["spine_width_pt"] == [1.5]
    assert matplotlib.rcParams["axes.linewidth"] == before
    res = worker.render({**base, "out": str(tmp_path / "b.svg")})
    assert res["summary"]["spine_width_pt"] == [before]
    res = worker.render({**base, "out": str(tmp_path / "c.svg"), "rc": {"axes.no_such_key": 1}})
    assert not res["ok"] and "axes.no_such_key" in res["error"]
