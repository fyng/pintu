import shutil
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
    assert m == {"left": 12.0, "right": 1.5, "top": 1.0, "bottom": 9.0, "gap": 2.0, "tick": 4.0,
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


CONTRACT = Path(__file__).resolve().parent / "fixtures" / "stylepacks" / "contract"


def contract_pack(tmp_path):
    """A copy of the contract pack with matplotlib's DejaVu Sans and Serif linked into its font folders."""
    import matplotlib
    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    d = tmp_path / "contract"
    shutil.copytree(CONTRACT, d)
    (d / "fonts" / "DejaVuSans.ttf").symlink_to(ttf / "DejaVuSans.ttf")
    (d / "fonts-serif" / "DejaVuSerif.ttf").symlink_to(ttf / "DejaVuSerif.ttf")
    return d


def test_contract_pack_uses_every_key():
    doc = style.tomllib.loads((CONTRACT / "stylepack.toml").read_text())
    assert set(doc) == style._TOP
    for table, keys in style.KEYS.items():
        if table == "presets.*":
            used = set().union(*doc["presets"].values())
        elif table == "lint.rules":
            used = set().union(*doc["lint"]["rules"])
        else:
            t = doc
            for part in table.split("."):
                t = t[part]
            used = set(t)
        assert used == keys, table


def test_contract_pack_loads(tmp_path):
    p = style.load(contract_pack(tmp_path))
    assert p.schema == 1 and p.source == {"url": "https://example.org/guide", "ref": "v0.0-test"}
    assert p.rc["axes.spines.top"] is False and p.letter["color"] == "#1a2b3c"
    wide, cell = p.view("wide"), p.view("cell")
    assert wide.fonts == ("DejaVu Sans", "Liberation Sans") and cell.fonts == ("DejaVu Serif",)
    assert cell.font_size_pt == 6 and cell.preset("cell")["font_size_pt"] == 6 and wide.font_size_pt == 6.5
    assert [q.name for q in cell.font_paths] == ["fonts-serif"] and [q.name for q in wide.font_paths] == ["fonts"]
    assert [q.name for q in p.all_font_paths()] == ["fonts", "fonts-serif"]
    assert "dejavu serif" in style.typst_families(cell.font_paths)
    # Lint merges per key; rules merge by id.
    assert cell.text_pt == (6, 8) and cell.size_tol_mm == 0.2 and wide.text_pt == (5, 7)
    assert [(r.id, r.min) for r in cell.rules] == [("tick-labels", 6), ("spine_width_pt", None)]
    assert [(r.id, r.min) for r in wide.rules] == [("tick-labels", 5), ("spine_width_pt", None)]
    assert cell.rc == {**p.rc, "axes.linewidth": 0.75, "font.family": ["DejaVu Serif"]}
    assert p.preset("cell")["rc"] == cell.rc and p.preset("wide")["rc"] == p.rc
    assert p.view("nope") is wide and cell.view("wide") is wide
    lt = p.preset("cell")["letter"]
    assert lt["font"] == ("DejaVu Serif",) and lt["color"] == "#000000" and lt["lower"] and lt["weight"] == 700
    assert style.warnings(p) == []


def test_schema_version(tmp_path):
    assert style.load(write(tmp_path, MINIMAL)).schema == 1
    newer = MINIMAL.replace('[pack]\n', '[pack]\nschema = 2\n') + "[future]\nx = 1\n"
    with pytest.raises(style.StylePackError, match="schema 2 is newer than this pintu supports \\(1\\)"):
        style.load(write(tmp_path, newer, "b"))
    with pytest.raises(style.StylePackError, match="schema must be an integer"):
        style.load(write(tmp_path, MINIMAL.replace('[pack]\n', '[pack]\nschema = "1"\n'), "c"))
    with pytest.raises(style.StylePackError, match="source must be a table"):
        style.load(write(tmp_path, MINIMAL.replace('[pack]\n', '[pack]\nsource = {commit = "x"}\n'), "d"))


@pytest.mark.parametrize("extra, match", [
    ("[letter]\ncolor = 'red'\n", "color must be a hex colour"),
    ("[letter]\nfont = []\n", "font must be a font name"),
    ("[matplotlib]\nrc = {axes = {linewidth = 1}}\n", "rc 'axes' must be a string"),
    ("[matplotlib]\nrc = 3\n", "rc must be a table"),
    ("[matplotlib]\nstyle = 'x'\n", "unknown key"),
    ("[margins.scale]\ndiscount = 0.5\n", "ref_mm is required"),
    ("[margins.scale]\nref_mm = [30]\n", "ref_mm must be"),
    ("[margins.scale]\nref_mm = [30, 24]\ndiscount = 2\n", "discount must be 0-1"),
    ("[margins.scale]\nref_mm = [30, 24]\nfixed = {left = 20}\n", "fixed.left 20 is above the margin 12"),
    ("[margins.scale]\nref_mm = [30, 24]\nfixed = {inner = 1}\n", "unknown margin"),
    ("[margins.scale]\nref_mm = [30, 24]\nlog = true\n", "unknown key"),
])
def test_new_key_validation_errors(tmp_path, extra, match):
    with pytest.raises(style.StylePackError, match=match):
        style.load(write(tmp_path, MINIMAL + extra))


def test_preset_override_validation(tmp_path):
    for i, (line, match) in enumerate([
            ("fonts = {family = 'Arial'}", "family must be a non-empty list"),
            ("fonts = {colour = 1}", "unknown key"),
            ("lint = {text_pt = [8, 6]}", "min 8 is above max 6"),
            ("matplotlib = {rc = {x = {y = 1}}}", "must be a string"),
            ("margins = {scale = {ref_mm = [30, 24], fixed = {left = 13}}}", "above the margin")]):
        with pytest.raises(style.StylePackError, match=match):
            style.load(write(tmp_path, MINIMAL + line + "\n", f"p{i}"))


def test_notes_cap(tmp_path, capsys):
    def at(n):
        return MINIMAL.replace('[pack]\n', '[pack]\nnotes = [' + ", ".join(['"' + "x" * 99 + '"'] * n) + ']\n')
    ok = style.load(write(tmp_path, at(40), "a"))
    assert style.notes_bytes(ok.notes) == 4000 and style.warnings(ok) == []
    warn = style.load(write(tmp_path, at(41), "b"))
    assert style.warnings(warn) == ["notes: 4100 bytes, above the 4096-byte budget; every agent prompt carries them"]
    with pytest.raises(style.StylePackError, match="notes are 16400 bytes; the limit is 16384"):
        style.load(write(tmp_path, at(164), "c"))
    assert style.check(tmp_path / "b") == 0
    assert "warning: notes: 4100 bytes" in capsys.readouterr().out


def test_stylepack_check_cli(tmp_path, capsys):
    from pintu import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["stylepack", "check", str(contract_pack(tmp_path))])
    out = capsys.readouterr().out
    assert e.value.code == 0
    assert "pack 'contract' (schema 1, source https://example.org/guide @ v0.0-test): ok" in out
    assert "preset wide (default): col1 89, full 183 mm; max height 170 mm; font DejaVu Sans" in out
    assert "preset cell: col1 85, full 174 mm; max height 200 mm; font DejaVu Serif" in out
    assert "Style rules (pack 'contract', preset 'cell')" in out and "tick labels are 6 pt" in out
    assert "warning" not in out
    bad = write(tmp_path, MINIMAL + "[letter]\ncase = 'title'\n", "bad")
    with pytest.raises(SystemExit) as e:
        cli.main(["stylepack", "check", str(bad)])
    out = capsys.readouterr().out
    assert e.value.code == 1 and out.startswith("error: ") and "case must be one of" in out


def test_rules_text_uses_preset_overrides(tmp_path):
    p = style.load(contract_pack(tmp_path))
    cell, wide = style.rules_text(p, "cell"), style.rules_text(p, "wide")
    assert "All text 6-8 pt; font DejaVu Serif" in cell and "Size of drawn tick labels: 6-6 pt" in cell
    assert "All text 5-7 pt; font DejaVu Sans, Liberation Sans" in wide and "5-5 pt" in wide
    assert "matplotlib rcParams" in cell
    assert "rcParams" not in style.rules_text(style.default(), None)


def test_margin_scale_rule(tmp_path):
    p = style.load(contract_pack(tmp_path))
    wide = p.preset("wide")
    base = style.margins(wide)
    assert base["left"] == 8.8 and style.margins(wide, 30, 24) == base  # the reference cell
    # 60 x 48 mm: s = 2, k = 1 + 0.5 * (2 - 1) = 1.5; fixed parts do not scale.
    m = style.margins(wide, 60, 48)
    assert m["left"] == pytest.approx(6.6 + 2.2 * 1.5) and m["top"] == pytest.approx(2 + 1.6 * 1.5)
    assert m["right"] == 2 and m["gap"] == pytest.approx(3) and m["tick"] == pytest.approx(7.8)
    # The preset overrides the discount only: k = 1 + 0.25 * (2 - 1) = 1.25.
    c = style.margins(p.preset("cell"), 60, 48)
    assert c["left"] == pytest.approx(6.6 + 3.4 * 1.25) and c["gap"] == pytest.approx(2.5)
    # Without a rule, margins are fixed at any size.
    d = style.get("nature")
    assert d["margin_scale"] is None and style.margins(d, 183, 120) == style.margins(d)


def test_letter_color_and_font_codegen(tmp_path):
    import typst
    from pintu.render import library_source
    p = style.load(contract_pack(tmp_path))
    text = "version: 1\npage: {width: 100, height: 80, style: %s}\npanels:\n  - {id: p, cell: [0, 0, 36, 36]}\n"
    src = codegen.generate(Board.loads(text % "wide", p), lambda f: False, "x")
    assert 'letter: "A"' in src and 'letter-fill: rgb("#1a2b3c")' in src and 'letter-font: ("DejaVu Sans",)' in src
    src = codegen.generate(Board.loads(text % "cell", p), lambda f: False, "x")
    assert 'letter-fill: rgb("#000000")' in src and 'letter-font: ("DejaVu Serif",)' in src
    # The library takes the new arguments.
    (tmp_path / "boards" / "build").mkdir(parents=True)
    (tmp_path / "boards" / "build" / codegen.LIBRARY).write_text(library_source())
    svg = typst.compile(src.encode(), format="svg", root=str(tmp_path),
                        font_paths=style.typst_fonts(p.all_font_paths()))
    assert b"<svg" in svg
