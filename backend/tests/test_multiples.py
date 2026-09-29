import asyncio

import pytest

from pintu import multiples as mult
from pintu import style as styles
from pintu.board import Board, BoardError
from pintu.cache import cache_key
from pintu.project import Project
from pintu.recipes import RenderRequest, SubprocessRunner
from pintu.renders import request_for
from pintu.server import apply_ops, recipe_panel_id

M = styles.margins("nature")


def test_parse_spans_and_empties():
    nrows, ncols, spans = mult.parse([["A", "A", "B"], ["C", ".", "B"]])
    assert (nrows, ncols) == (2, 3)
    assert spans == {"A": (0, 0, 1, 2), "B": (0, 2, 2, 3), "C": (1, 0, 2, 1)}
    assert list(spans) == ["A", "B", "C"]  # reading order
    assert mult.parse([[1, "."]])[2] == {"1": (0, 0, 1, 1)}


@pytest.mark.parametrize("bad, msg", [
    ([], "non-empty"),
    ([["A"], ["B", "C"]], "same length"),
    ([["A", "B", "A"]], "rectangle"),
    ([["A", "B"], ["B", "B"]], "rectangle"),
    ([[["A"]]], "nested"),
])
def test_parse_rejects(bad, msg):
    with pytest.raises(mult.MosaicError, match=msg):
        mult.parse(bad)


def test_share_modes():
    assert mult.share_of(None) == {"x": "all", "y": "all"}
    assert mult.share_of("row") == {"x": "row", "y": "row"}
    assert mult.share_of({"y": "col"}) == {"x": "all", "y": "col"}
    for bad in ("some", {"z": "all"}, {"x": "both"}):
        with pytest.raises(mult.MosaicError):
            mult.share_of(bad)


def test_cell_size_grows_gaps_for_inner_tick_labels():
    shared = mult.cell_size(120, 60, 2, 3, M, {"x": "all", "y": "all"})
    free = mult.cell_size(120, 60, 2, 3, M, {"x": "none", "y": "none"})
    assert free[0] == pytest.approx(shared[0] - 2 * M["tick"] / 3)
    assert free[1] == pytest.approx(shared[1] - M["tick"] / 2)


def test_reflow_suggestion():
    m = {"item": "c", "mosaic": [["A", "B", "C", "D", "E"]]}
    # Wide enough: no reflow.
    assert mult.reflow(m, 280, 40, (40, 16), M) is None
    # Too narrow for five in a row: 2 x 3 in reading order.
    assert mult.reflow(m, 150, 60, (40, 16), M) == [["A", "B", "C"], ["D", "E", "."]]
    # No min_cell: never.
    assert mult.reflow(m, 20, 20, None, M) is None
    # Nothing fits: the closest grid, unless it is the current one.
    assert mult.reflow({"item": "c", "mosaic": [["A"]]}, 20, 20, (40, 16), M) is None


def test_recipe_meta(tmp_path):
    f = tmp_path / "r.py"
    f.write_text("from pintu_sdk import multiples, choice\n"
                 "@multiples(item=choice(list), min_cell=(25, 20))\ndef one(ax, cohort, k=1): ...\n"
                 "def plain(w, h): ...\n")
    assert mult.recipe_meta(f, "one") == {"item": "cohort", "min_cell": [25.0, 20.0]}
    assert mult.recipe_meta(f, "plain") is None


BOARD = """\
version: 1
page: {width: 183, height: 120, grid: [36, 36], gutter: 3, style: nature}
panels:
  - id: km
    cell: [0, 0, 36, 12]
    source:
      recipe: rec:one   # the recipe
      multiples: {item: cohort, mosaic: [[A, B, C]], share: {x: all, y: row}}
  - id: t
    cell: [0, 12, 18, 36]
    source: {recipe: rec:one, params: {cohort: X, k: 2}}
"""


def test_board_round_trip_and_edit_keeps_comments():
    b = Board.loads(BOARD)
    assert b.dumps() == BOARD
    b.set_multiples("km", mosaic=[["C", "B", "A"]])
    assert b.dumps() == BOARD.replace("[[A, B, C]]", "[[C, B, A]]")
    b.set_multiples("km", mosaic=[["C", "B"], ["A", "."]], width_ratios=[2, 1])
    out = b.dumps()
    assert "mosaic: [[C, B], [A, .]]" in out and "width_ratios: [2, 1]" in out
    assert Board.loads(out).dumps() == out
    b.set_multiples("km", mosaic=[["C", "B", "A"]])  # new shape: stale ratios go
    assert "width_ratios" not in b.dumps()
    b.set_multiples("km", share=None)
    assert "share" not in b.panel("km")["source"]["multiples"]


def test_make_multiples_from_item_param():
    b = Board.loads(BOARD)
    b.set_multiples("t", item="cohort")
    src = b.panel("t")["source"]
    assert src["multiples"]["mosaic"] == [["X"]] and dict(src["params"]) == {"k": 2}
    assert "source: {recipe: rec:one, params: {k: 2}, multiples: {item: cohort, mosaic: [[X]]}}" in b.dumps()
    with pytest.raises(BoardError, match="rectangle"):
        b.set_multiples("t", mosaic=[["X", "Y", "X"]])
    with pytest.raises(BoardError, match="width_ratios"):
        b.set_multiples("t", width_ratios=[1, 2])


def test_invalid_multiples_in_file():
    with pytest.raises(BoardError, match="rectangle"):
        Board.loads(BOARD.replace("[[A, B, C]]", "[[A, B, A]]"))
    with pytest.raises(BoardError, match="share"):
        Board.loads(BOARD.replace("y: row", "y: diag"))


def test_request_and_cache_key_include_mosaic_and_share():
    b = Board.loads(BOARD)
    req = request_for(b, b.panel("km"))
    assert req.multiples == {"mosaic": [["A", "B", "C"]], "share": {"x": "all", "y": "row"}, "margins": M}
    b2 = Board.loads(BOARD.replace("[[A, B, C]]", "[[B, A, C]]"))
    b3 = Board.loads(BOARD.replace("y: row", "y: none"))
    keys = {cache_key(request_for(x, x.panel("km")), "h") for x in (b, b2, b3)}
    assert len(keys) == 3
    assert request_for(b, b.panel("t")).multiples is None


def test_group_letters_and_bands():
    b = Board.loads("""\
page: {width: 183, height: 120, grid: 36, gutter: 3}
panels:
  - {id: a, cell: [0, 0, 12, 12]}
  - {id: b, cell: [12, 2, 24, 12]}
  - {id: c, cell: [12, 12, 24, 24]}
  - {id: d, cell: [24, 0, 36, 12]}
  - {id: e, cell: [0, 12, 12, 24]}
groups:
  - {id: g, panels: [b, c, d]}
""")
    # Units in reading order: a (0,0), g (bbox 12..36, top 0), e (0,12).
    assert b.unit_letters() == {"a": "a", "g": "b", "e": "c"}
    assert b.group_cell(b.group("g")) == (12, 0, 36, 24)
    # The letter sits on the leftmost member on the group's top edge (d); b and c draw none.
    assert b.letters() == {"a": "a", "b": None, "c": None, "d": "b", "e": "c"}
    bands = b.bands()
    assert bands["d"] == 3.5 and bands["b"] == 0 and bands["c"] == 0 and bands["a"] == 3.5
    # A group letter override, and set_letter through a member.
    b.set_letter("c", "z")
    assert b.group("g")["letter"] == "z" and b.letters()["d"] == "z"
    b.set_letter("g", None)
    assert b.bands()["d"] == 0 and b.letters()["d"] is None
    assert Board.loads(b.dumps()).dumps() == b.dumps()


def test_group_ops_round_trip():
    b = Board.new(height=120)
    for i in range(3):
        b.add_panel([12 * i, 0, 12 * i + 12, 12], pid="p")
    gid = b.add_group(["p", "p-2"])
    assert gid == "group" and "groups:\n  - {id: group, panels: [p, p-2]}\n" in b.dumps()
    assert b.letters() == {"p": "a", "p-2": None, "p-3": "b"}
    assert Board.loads(b.dumps()).dumps() == b.dumps()
    b.remove_panel("p-2")  # a one-member group dissolves
    assert "groups" not in b.dumps() and b.letters() == {"p": "a", "p-3": "b"}
    b.add_group(["p", "p-3"], "g")
    b.ungroup("g")
    assert "groups" not in b.doc


@pytest.mark.parametrize("bad, msg", [
    ("groups:\n  - {id: g, panels: [a]}\n", "at least 2"),
    ("groups:\n  - {id: g, panels: [a, zz]}\n", "no panel"),
    ("groups:\n  - {id: a, panels: [a, b]}\n", "duplicate"),
    ("groups:\n  - {id: g, panels: [a, b]}\n  - {id: h, panels: [b, a]}\n", "more than one"),
])
def test_invalid_groups(bad, msg):
    with pytest.raises(BoardError, match=msg):
        Board.loads("panels:\n  - {id: a, cell: [0, 0, 4, 4]}\n  - {id: b, cell: [4, 0, 8, 4]}\n" + bad)


def test_recipe_drop_ids(demo):
    project = Project.open(demo)
    b = Board.new()
    apply_ops(project, b, [
        {"op": "add", "cell": [0, 0, 12, 12], "recipe": "recipes.cohort:timeline",
         "params": {"arm": "A", "patient": "S002", "stage": "I"}},
        {"op": "add", "cell": [12, 0, 24, 12], "recipe": "recipes.cohort:timeline",
         "params": {"arm": "A", "patient": "S002", "stage": "I"}},
        {"op": "add", "cell": [24, 0, 36, 12], "recipe": "recipes.km:km"},
    ])
    assert [p["id"] for p in b.panels] == ["timeline-S002", "timeline-S002-2", "km"]
    assert recipe_panel_id(project, "m:f", {"x": 1}) == "f-1"


PLAIN = '''from matplotlib.figure import Figure


def grid(w, h, mosaic, share):
    fig = Figure(figsize=(w / 25.4, h / 25.4))
    axs = fig.subplot_mosaic(mosaic, sharex=share["x"] == "all", sharey=share["y"] == "all")
    fig.suptitle(" ".join(v for row in mosaic for v in row) + " " + share["x"] + share["y"])
    return fig
'''

SDK = '''from pintu_sdk import choice, multiples


@multiples(item=choice(["A", "B", "C"]), min_cell=(20, 10))
def one(ax, cohort):
    ax.plot([0, 1], [0, 1], label="fit")
    ax.set(xlabel="x", ylabel="y", title=cohort)
'''


def test_render_with_and_without_sdk(tmp_path):
    (tmp_path / "plain.py").write_text(PLAIN)
    (tmp_path / "sdk.py").write_text(SDK)
    project = Project.open(tmp_path)
    b = Board.loads("""\
page: {width: 183, height: 120, grid: 36, gutter: 3}
panels:
  - id: p
    cell: [0, 0, 36, 18]
    letter: false
    source: {recipe: plain:grid, multiples: {item: cohort, mosaic: [[A, B], [C, .]], share: {x: all, y: none}}}
  - id: s
    cell: [0, 18, 36, 36]
    letter: false
    source: {recipe: sdk:one, multiples: {item: cohort, mosaic: [[A, B], [C, .]], share: {x: all, y: none}}}
""")
    runner = SubprocessRunner(project)
    plain = asyncio.run(runner.render(request_for(b, b.panel("p"))))
    assert plain.ok, plain.error
    assert any(t["text"] == "A B C . allnone" for t in plain.summary["texts"])
    res = asyncio.run(runner.render(request_for(b, b.panel("s"))))
    assert res.ok, res.error
    axes = {a["title"]: a for a in res.summary["axes"]}
    assert set(axes) == {"A", "B", "C"}
    # Outer edges only: B has C's column empty below it, so it keeps its x label; A does not.
    assert axes["A"]["xlabel"] == "" and axes["B"]["xlabel"] == "x" and axes["C"]["xlabel"] == "x"
    assert axes["B"]["ylabel"] == "" and axes["A"]["ylabel"] == "y"
    # One shared key.
    assert sum(t["text"] == "fit" for t in res.summary["texts"]) == 1
    # The left margin comes from the style's margins.
    x0 = min(a["bbox_mm"][0] for a in res.summary["axes"])
    assert x0 == pytest.approx(M["left"], abs=0.05)
    assert (tmp_path / res.svg).is_file() and "__multiples__" not in res.svg
