import json
import subprocess
import sys

import pytest

from pintu_sdk import choice, multiples
from pintu_sdk.multiples import MARGINS, MARGINS_ENV, layout, parse_mosaic


def test_import_does_not_load_matplotlib():
    code = "import sys, pintu_sdk; print('matplotlib' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip() == "False"


def test_choice_values():
    assert choice(lambda: ["a", "b"]).values() == ["a", "b"]
    assert choice(("x",)).values() == ["x"]


def test_parse_mosaic():
    assert parse_mosaic([["A", "A"], ["B", "."]]) == (2, 2, {"A": (0, 0, 1, 2), "B": (1, 0, 2, 1)})
    with pytest.raises(ValueError, match="rectangle"):
        parse_mosaic([["A", "B"], ["B", "A"]])


def test_layout_spans_ratios_and_key():
    m = dict(MARGINS)
    r = layout(100, 50, [["A", "B"], ["C", "C"]], "all", width_ratios=[3, 1], m=m)
    aw = 100 - m["left"] - m["right"] - m["gap"]
    assert r["A"][0] == m["left"] and r["A"][2] == pytest.approx(aw * 3 / 4)
    assert r["C"][2] == pytest.approx(aw + m["gap"])  # spans both columns and the gap
    assert r["A"][1] == m["top"] + m["title"]
    keyed = layout(100, 50, [["A", "B"], ["C", "C"]], "all", width_ratios=[3, 1], m=m, key=True)
    assert keyed["A"][1] == r["A"][1] + m["key"]
    assert r["C"][1] + r["C"][3] == pytest.approx(50 - m["bottom"])


def one_fig(share, mosaic=(("A", "B"), ("C", "D"))):
    @multiples(item=choice(["A", "B", "C", "D"]), min_cell=(10, 10))
    def one(ax, v, scale=1):
        ax.plot([0, 1], [0, scale * (ord(v) - 64)], label="line")
        ax.set(xlabel="x", ylabel="y", title=v)
        ax.legend()

    return one(80, 60, mosaic=[list(r) for r in mosaic], share=share)


def axes_by_title(fig):
    return {ax.get_title(): ax for ax in fig.axes}


@pytest.mark.parametrize("share, same_y", [
    ("all", {("A", "D")}), ("row", {("A", "B"), ("C", "D")}), ("col", {("A", "C"), ("B", "D")}), ("none", set()),
])
def test_share_modes(share, same_y):
    a = axes_by_title(one_fig(share))
    pairs = {(p, q) for p in a for q in a if p < q and a[p].get_shared_y_axes().joined(a[p], a[q])}
    assert same_y <= pairs
    if share == "all":
        assert len(pairs) == 6
    if share == "none":
        assert not pairs


def test_outer_edge_labels_and_one_key():
    fig = one_fig({"x": "all", "y": "row"}, (("A", "B"), ("C", ".")))
    a = axes_by_title(fig)
    assert a["A"].get_xlabel() == "" and a["B"].get_xlabel() == "x"  # B has an empty cell below
    assert a["B"].get_ylabel() == "" and a["C"].get_ylabel() == "y"
    assert not any(ax.get_legend() for ax in fig.axes) and len(fig.legends) == 1
    assert [t.get_text() for t in fig.legends[0].get_texts()] == ["line"]


def test_single_item_and_env_margins(monkeypatch):
    @multiples(item=choice(["A"]))
    def one(ax, v, extra=None):
        ax.set_title(f"{v}{extra}")

    monkeypatch.setenv(MARGINS_ENV, json.dumps({"left": 20.0}))
    fig = one(50, 30, v="Q", extra="!")
    (ax,) = fig.axes
    assert ax.get_title() == "Q!"
    assert ax.get_position().x0 * 50 == pytest.approx(20.0)
    assert one.__pintu_multiples__["item"] == "v"
