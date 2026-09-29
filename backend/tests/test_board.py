import pytest

from pintu.board import Board, BoardError

TEXT = """\
# my figure
version: 1
page: {width: 183, height: 170, grid: [36, 36], gutter: 3, style: nature}
panels:
  - id: km
    cell: [0, 0, 36, 6]
    note: keep me          # unknown key, with a comment
    source:
      recipe: analysis.km.plot_km:km_one
      multiples: {item: cohort, mosaic: [[BRCA, LUAD, COAD, PRAD, PAAD]], share: {x: all, y: row}}
  - id: timeline
    letter: h
    cell: [18, 24, 36, 36]
    source: {file: outputs/timelines/P-0064072.pdf}   # static: no re-render
extra: {owner: me}
"""


def test_round_trip_is_byte_stable():
    assert Board.loads(TEXT).dumps() == TEXT


def test_edit_keeps_unknown_keys_order_and_comments():
    b = Board.loads(TEXT)
    b.set_cell("timeline", [18, 24, 30, 36])
    out = b.dumps()
    assert out == TEXT.replace("cell: [18, 24, 36, 36]", "cell: [18, 24, 30, 36]")


def test_schema_reads_recipe_and_letters():
    b = Board.loads(TEXT)
    assert b.page.nx == 36 and b.page.width == 183
    assert b.panel("km")["source"]["multiples"]["item"] == "cohort"
    assert b.letters() == {"km": "a", "timeline": "h"}


def test_add_split_letter_ops():
    b = Board.new()
    pid = b.add_panel([0, 0, 36, 12], "plots/a b.pdf")
    assert pid == "a-b"
    ids, exact = b.split(pid, 3)
    assert ids == ["a-b", "a-b-2", "a-b-3"] and exact
    b.set_letter("a-b-2", None)
    assert b.letters() == {"a-b": "a", "a-b-2": None, "a-b-3": "b"}
    b.set_letter("a-b-2", "auto")
    assert "letter" not in b.panel("a-b-2")
    b.validate()
    again = Board.loads(b.dumps())
    assert again.dumps() == b.dumps()
    assert "cell: [0, 0, 12, 12]" in b.dumps()


@pytest.mark.parametrize("bad, msg", [
    ("version: 2\npanels: []\n", "version"),
    ("panels:\n  - {id: a, cell: [0, 0, 40, 2]}\n", "outside"),
    ("panels:\n  - {id: a, cell: [0, 0, 4, 4]}\n  - {id: b, cell: [2, 2, 6, 6]}\n", "overlapping"),
    ("panels:\n  - {id: a, cell: [0, 0, 4, 4]}\n  - {id: a, cell: [4, 4, 6, 6]}\n", "duplicate"),
    ("panels:\n  - {id: a, cell: [0, 0, 1.5, 4]}\n", "integers"),
    ("- a\n", "mapping"),
    ("page: {letter_band: -1}\npanels: []\n", "letter_band"),
])
def test_invalid_boards(bad, msg):
    with pytest.raises(BoardError, match=msg):
        Board.loads(bad)


def test_letter_band_and_content_rect():
    b = Board.loads("""\
page: {width: 183, height: 173, grid: 36, gutter: 3}
panels:
  - {id: a, cell: [0, 0, 18, 18], source: {recipe: "m:f"}}
  - {id: b, cell: [18, 0, 36, 18], letter: false}
  - {id: c, cell: [0, 18, 36, 19]}
""")
    assert b.letter_band == 3.5  # Nature preset
    x, y, w, h = b.page.rect([0, 0, 18, 18])
    assert b.bands() == {"a": 3.5, "b": 0.0, "c": b.page.rect([0, 18, 36, 19])[3] / 2}
    assert b.content_rect(b.panel("a")) == (x, y + 3.5, w, h - 3.5)
    assert b.content_rect(b.panel("b")) == b.page.rect([18, 0, 36, 18])
    b.set_page(letter_band=5)
    assert b.bands()["a"] == 5.0 and b.content_rect(b.panel("a"))[3] == h - 5
