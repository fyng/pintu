// pintu board assembly library. Generated board files import it and place each
// panel by its grid cell. Geometry: pitch p = (L + g) / N; a panel between grid
// lines a and b starts at a·p and is (b − a)·p − g long.

// The span between grid lines `a` and `b` on an axis of length `len` with `n`
// units: (at: start, len: length).
#let grid-span(len, n, a, b, gutter: 3mm) = {
  let p = (len + gutter) / n
  (at: a * p, len: (b - a) * p - gutter)
}

// `fig-span` from academic-design-system (formats/publication/fig.typ): `n`
// equal units of a span with gutters between them; the `k` units from unit `i`.
#let fig-span(of, n, i, k: 1, gutter: 3mm) = {
  let s = if type(of) == length { (at: 0mm, len: of) } else { of }
  let u = (s.len - (n - 1) * gutter) / n
  (at: s.at + i * (u + gutter), len: k * u + (k - 1) * gutter)
}

// Page setup: exact size, no margin.
#let board-page(width: 183mm, height: 170mm, font: (), size: 7pt, body) = {
  set page(width: width, height: height, margin: 0pt)
  set text(font: font, size: size, fallback: true)
  set par(spacing: 0em)
  body
}

// Draws a panel letter at the top-left of its letter band.
#let board-letter(letter, band: 3.5mm, size: 8pt, weight: 700) = box(height: band,
  align(left + top, text(size: size, weight: weight, letter)))

// A placeholder for a panel with no drawable source.
#let board-empty(w, h, label, band: 0mm) = block(width: w, height: h, fill: luma(245),
  stroke: (paint: luma(160), thickness: 0.4pt, dash: "dashed"),
  inset: (left: 1mm, top: band + 1.2mm, right: 1mm, bottom: 1mm),
  text(size: 5.5pt, fill: luma(110), label))

// One panel at grid cell (x0, y0, x1, y1). `src` is a root-relative path
// ("/…") or none; `kind` is "vector" or "raster". A lettered panel reserves a
// full-width band of height `band` at its top for the letter; the source fills
// the area below it (band 0: the whole cell).
#let board-panel(page-w, page-h, grid, cell, gutter: 3mm, src: none, kind: "vector",
  label: "", letter: none, band: 0mm, letter-size: 8pt, letter-weight: 700) = {
  let x = grid-span(page-w, grid.at(0), cell.at(0), cell.at(2), gutter: gutter)
  let y = grid-span(page-h, grid.at(1), cell.at(1), cell.at(3), gutter: gutter)
  place(top + left, dx: x.at, dy: y.at, block(width: x.len, height: y.len, clip: true, {
    if src == none {
      board-empty(x.len, y.len, label, band: band)
    } else {
      place(top + left, dy: band, image(src, width: x.len, height: y.len - band, fit: "contain"))
    }
    if letter != none {
      place(top + left, board-letter(letter, band: band, size: letter-size, weight: letter-weight))
    }
  }))
}
