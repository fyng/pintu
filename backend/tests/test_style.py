from pathlib import Path

import pytest

from pintu import codegen, style
from pintu.board import Board
from pintu.project import Project

STRICT = Path(__file__).resolve().parents[2] / "examples" / "stylepacks" / "strict"

# The hard-coded preset style.py held before style packs.
OLD_NATURE = {
    "name": "nature",
    "widths": {"col1": 89.0, "col15": 136.0, "full": 183.0},
    "max_height": 170.0,
    "font": ["IBM Plex Sans", "Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
    "font_size_pt": 7.0,
    "letter": {"size_pt": 8.0, "lower": True, "band_mm": 3.5},
}

MINIMAL = """\
[pack]
name = "mini"
default_preset = "a"

[presets.a]
widths = {full = 100}
max_height = 80
"""


def write(tmp_path, text, name="pack"):
    d = tmp_path / name
    d.mkdir()
    (d / "stylepack.toml").write_text(text)
    return d


def test_default_pack_matches_old_preset():
    p = style.get("nature")
    for k, v in OLD_NATURE.items():
        if k == "letter":
            assert {q: p["letter"][q] for q in v} == v
        else:
            assert p[k] == v
    assert p["letter"]["weight"] == 700 and p["letter"]["case"] == "lower"
    assert style.get("anything") is style.get("nature")  # unknown names give the default preset
    assert style.default().text_pt == (5.0, 7.0) and style.default().size_tol_mm == 0.1
    assert style.default().rules == () and style.default().typst == ""


def test_margins_accessor():
    m = style.margins(style.get("nature"))
    assert m == {"left": 12.0, "right": 1.5, "top": 1.0, "bottom": 7.5, "gap": 2.0, "tick": 4.0,
                 "title": 3.0, "key": 3.5}
    m["left"] = 99  # a copy
    assert style.margins(style.get("nature"))["left"] == 12.0
    strict = style.load(STRICT)
    assert style.margins(strict.preset("nature"))["left"] == 8.8


def test_minimal_pack_defaults(tmp_path):
    p = style.load(write(tmp_path, MINIMAL))
    assert p.name == "mini" and p.fonts == ("DejaVu Sans",) and p.letter["band_mm"] == 3.5
    assert p.preset("zzz")["widths"] == {"full": 100.0}
    assert style.load(tmp_path / "pack" / "stylepack.toml") is p  # file or folder; cached


def test_strict_pack_presets_and_rules():
    p = style.load(STRICT)
    assert set(p.presets) == {"nature", "science", "cell"}
    sci = p.preset("science")
    assert sci["letter"]["size_pt"] == 10 and sci["letter"]["upper"] and not sci["letter"]["lower"]
    assert sci["letter"]["band_mm"] == 3.5 and p.letter["weight"] == 700
    assert [r.id for r in p.rules] == ["tick-labels", "tick-length", "tick-width", "axis-lines", "data-lines"]
    assert p.rules[0].allowed() == "5-6 pt"


@pytest.mark.parametrize("extra, match", [
    ("[colour]\nx = 1\n", "unknown table"),
    ("[letter]\ncase = 'title'\n", "case must be one of"),
    ("[letter]\nweight = 1000\n", "weight must be"),
    ("[letter]\nband_mm = -1\n", "band_mm must be a number"),
    ("[margins]\ninner = 3\n", "unknown key"),
    ("[fonts]\nfamily = []\n", "family must be a non-empty list"),
    ("[fonts]\npaths = ['nope']\n", "font folder not found"),
    ("[lint]\ntext_pt = [7, 5]\n", "min 7 is above max 5"),
    ("[[lint.rules]]\nproperty = 'colour'\nmin = 1\n", "property must be one of"),
    ("[[lint.rules]]\nproperty = 'tick_length_pt'\n", "give min and/or max"),
    ("[[lint.rules]]\nproperty = 'tick_length_pt'\nmin = 3\nmax = 1\n", "min 3 is above max 1"),
    ("[typst]\nsnippet = 3\n", "snippet must be a string"),
])
def test_validation_errors(tmp_path, extra, match):
    with pytest.raises(style.StylePackError, match=match):
        style.load(write(tmp_path, MINIMAL + extra))


def test_validation_structure(tmp_path):
    with pytest.raises(style.StylePackError, match="name is required"):
        style.load(write(tmp_path, MINIMAL.replace('name = "mini"\n', ""), "a"))
    with pytest.raises(style.StylePackError, match="default_preset must name a preset"):
        style.load(write(tmp_path, MINIMAL.replace('"a"\n', '"b"\n', 1), "b"))
    with pytest.raises(style.StylePackError, match="max_height is required"):
        style.load(write(tmp_path, MINIMAL.replace("max_height = 80\n", ""), "c"))
    with pytest.raises(style.StylePackError, match="invalid TOML"):
        style.load(write(tmp_path, "[pack\n", "d"))
    with pytest.raises(style.StylePackError, match="no stylepack.toml"):
        style.load(tmp_path)


def test_project_selects_pack(tmp_path):
    (tmp_path / "pintu.toml").write_text('[style]\npack = "packs/mini"\n')
    (tmp_path / "packs").mkdir()
    write(tmp_path / "packs", MINIMAL, "mini")
    assert style.for_project(Project.open(tmp_path)).name == "mini"
    (tmp_path / "pintu.toml").write_text('[style]\npack = "default"\n')
    assert style.for_project(Project.open(tmp_path)).name == "default"
    (tmp_path / "pintu.toml").write_text('[style]\npack = "gone"\n')
    with pytest.raises(style.StylePackError, match="not found"):
        style.for_project(Project.open(tmp_path))
    (tmp_path / "pintu.toml").write_text("")
    assert style.for_project(Project.open(tmp_path)).name == "default"


def test_board_uses_pack_preset(tmp_path):
    pack = style.load(write(tmp_path, MINIMAL.replace("max_height = 80\n", "max_height = 80\nletter = {band_mm = 5, case = 'upper', weight = 500}\n")
                            + '[typst]\nsnippet = "#set text(fill: red)"\n'))
    b = Board.loads("version: 1\npage: {width: 100, height: 80, style: a}\npanels:\n  - {id: p, cell: [0, 0, 36, 36]}\n", pack)
    assert b.letter_band == 5 and b.preset["name"] == "a"
    src = codegen.generate(b, lambda f: False, "x")
    assert 'letter: "A"' in src and "band: 5mm" in src and "letter-weight: 500" in src
    assert "#set text(fill: red)" in src
    assert Board.new(pack=pack).style == "a" and Board.new().style == "nature"
    # The default pack generates the same source as before packs: no weight argument.
    d = Board.loads("version: 1\npage: {width: 100, height: 80}\npanels:\n  - {id: p, cell: [0, 0, 36, 36]}\n")
    src = codegen.generate(d, lambda f: False, "x")
    assert "letter-weight" not in src and 'letter: "a"' in src and "band: 3.5mm" in src


def test_rules_text():
    t = style.rules_text(style.load(STRICT), "science")
    assert "Style rules (pack 'strict', preset 'science')" in t
    assert "57 mm (col1)" in t and "height at most 230 mm" in t and "All text 5-7 pt" in t
    assert "Size of drawn tick labels: 5-6 pt (tick labels are 5 pt" in t
    assert "Length of visible tick marks: 1-3 pt" in t and "Type roles:" in t
