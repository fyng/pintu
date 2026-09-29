import typst

from pintu import codegen
from pintu.board import Board
from pintu.project import Project
from pintu.render import Renderer


def test_generate_panels_letters_and_placeholders():
    b = Board.loads("""\
version: 1
page: {width: 183, height: 170, grid: [36, 30], gutter: 3}
panels:
  - {id: a, cell: [0, 0, 18, 6], source: {file: plots/a.pdf}}
  - {id: b, cell: [18, 0, 36, 6], source: {file: plots/missing.pdf}, letter: X}
  - {id: c, cell: [0, 6, 36, 12], source: {recipe: "m:f"}}
  - {id: d, cell: [0, 12, 36, 18], letter: false}
""")
    src = codegen.generate(b, lambda f: f == "plots/a.pdf", "fig")
    assert '#import "/boards/build/_pintu.typ": *' in src
    assert "#let G = (36, 30)" in src
    assert 'board-panel(W, H, G, (0, 0, 18, 6), gutter: 3mm, src: "/plots/a.pdf", kind: "vector", label: "a", letter: "a"' in src
    assert 'src: none, kind: "vector", label: "b: missing file plots/missing.pdf", letter: "x"' in src
    assert 'label: "c: recipe m:f (not rendered yet)", letter: "y"' in src
    assert 'label: "d", letter: none' in src


def test_string_escaping():
    assert codegen.typst_str('a"b\\c') == '"a\\"b\\\\c"'


def test_build_demo_svg_and_pdf(demo):
    p = Project.open(demo)
    b = Board.load(p.board_path("demo"))
    r = Renderer(p)
    svg, src, ms = r.svg("demo", b)
    assert svg.startswith(b"<svg") and ms > 0
    assert (demo / "boards/build/_pintu.typ").exists()
    pdf = r.write("demo", src)
    assert (demo / "boards/build/demo.typ").read_text() == src
    assert pdf.read_bytes().startswith(b"%PDF")
    # The page is 183 x 120 mm.
    assert b'viewBox="0 0 518.74' in svg
    assert r.thumb("plots/lines.pdf").startswith(b"<svg")


def test_panel_rect_matches_typst_layout(demo):
    """The generated file places the panel where geometry says."""
    p = Project.open(demo)
    b = Board.load(p.board_path("demo"))
    Renderer(p)
    lib = (demo / "boards/build/_pintu.typ").read_text()
    out = typst.query(
        (lib + "\n#metadata(grid-span(120mm, 36, 18, 36).len / 1mm) <v>\n").encode(),
        "<v>", field="value", one=True)
    assert abs(float(out) - b.page.rect(b.panel("heatmap")["cell"])[3]) < 1e-6
