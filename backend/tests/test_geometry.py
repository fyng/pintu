import json

import pytest
import typst

from pintu import geometry as geo
from pintu.render import library_source

N = 36
DIVISORS = [n for n in range(1, N + 1) if N % n == 0]
LENGTHS = [183.0, 170.0, 136.0, 89.0]


def cases():
    for n in DIVISORS:
        for i in range(n):
            for k in range(1, n - i + 1):
                yield n, i, k


def typst_spans(prelude: str, fn: str, length: float, root=None) -> list:
    """Evaluates even-span (or grid-span) for every case in one Typst run."""
    calls = ", ".join(
        f"{fn}({length}mm, {n}, {i}, k: {k}, gutter: 3mm)" if fn == "even-span"
        else f"{fn}({length}mm, {N}, {i * N // n}, {(i + k) * N // n}, gutter: 3mm)"
        for n, i, k in cases())
    src = f"{prelude}\n#metadata(({calls},).map(s => (s.at / 1mm, s.len / 1mm))) <out>\n"
    out = typst.query(src.encode(), "<out>", field="value", one=True, root=root)
    return json.loads(out)


def test_grid_matches_even_span_formula():
    for L in LENGTHS:
        for n, i, k in cases():
            a, b = i * N // n, (i + k) * N // n
            assert geo.span(L, N, a, b) == pytest.approx(geo.even_span(L, n, i, k), abs=0.01)


@pytest.mark.parametrize("L", LENGTHS)
def test_grid_matches_even_span_in_typst(L, tmp_path):
    (tmp_path / "lib.typ").write_text(library_source())
    ours = typst_spans('#import "/lib.typ": *', "grid-span", L, root=str(tmp_path))
    ref = typst_spans('#import "/lib.typ": *', "even-span", L, root=str(tmp_path))
    assert len(ours) == len(ref) == len(list(cases()))
    for (n, i, k), (at, ln), (rat, rln) in zip(cases(), ours, ref):
        pa, pl = geo.span(L, N, i * N // n, (i + k) * N // n)
        assert abs(at - rat) < 0.01 and abs(ln - rln) < 0.01, (n, i, k)
        assert abs(pa - rat) < 0.01 and abs(pl - rln) < 0.01, (n, i, k)


def test_nested_thirds_land_on_grid():
    # Thirds of the right half (even-span of a span) are grid lines 18, 24, 30.
    L, g = 183.0, 3.0
    half_at, half_len = geo.even_span(L, 2, 1)
    u = (half_len - 2 * g) / 3
    for j, line in enumerate((18, 24, 30)):
        assert geo.span(L, N, line, line + 6)[0] == pytest.approx(half_at + j * (u + g), abs=1e-9)


def test_unit_is_5_2mm_at_183():
    assert geo.pitch(183, 36) == pytest.approx(5.2, abs=0.05)
    assert geo.span(183, 36, 0, 1)[1] == pytest.approx(2.17, abs=0.01)


def test_snap():
    p = geo.pitch(183, 36)
    assert geo.snap(0.49 * p, 183, 36) == 0
    assert geo.snap(0.51 * p, 183, 36) == 1
    assert geo.snap(-5, 183, 36) == 0
    assert geo.snap(500, 183, 36) == 36


def test_overlaps():
    assert geo.overlaps((0, 0, 2, 2), (1, 1, 3, 3))
    assert not geo.overlaps((0, 0, 2, 2), (2, 0, 4, 2))
    assert geo.find_overlaps({"a": (0, 0, 2, 2), "b": (2, 0, 4, 2), "c": (1, 1, 3, 3)}) == [("a", "c"), ("b", "c")]


def test_letters_reading_order_and_overrides():
    cells = {"g": (12, 18, 36, 36), "a": (0, 0, 18, 6), "b": (18, 0, 36, 6), "f": (0, 18, 12, 36)}
    assert geo.assign_letters(cells, {}) == {"a": "a", "b": "b", "f": "c", "g": "d"}
    assert geo.assign_letters(cells, {"b": None}) == {"a": "a", "b": None, "f": "b", "g": "c"}
    assert geo.assign_letters(cells, {"f": "h"}) == {"a": "a", "b": "b", "f": "h", "g": "i"}
    assert geo.assign_letters(cells, {"g": "c"}) == {"a": "a", "b": "b", "f": "d", "g": "c"}


def test_split_exact_and_inexact():
    parts, exact = geo.split((0, 0, 36, 6), 3)
    assert exact and parts == [(0, 0, 12, 6), (12, 0, 24, 6), (24, 0, 36, 6)]
    parts, exact = geo.split((0, 0, 36, 6), 5)
    assert not exact and [p[2] - p[0] for p in parts] == [7, 7, 8, 7, 7]
    assert parts[0][0] == 0 and parts[-1][2] == 36
    parts, exact = geo.split((0, 0, 12, 12), 2, "y")
    assert exact and parts == [(0, 0, 12, 6), (0, 6, 12, 12)]
    with pytest.raises(ValueError):
        geo.split((0, 0, 2, 2), 3)
