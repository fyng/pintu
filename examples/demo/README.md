# pintu demo project

Two boards:

- `demo`: synthetic plots (`scripts/make_plots.py`) placed as static files.
- `recipes`: recipe panels drawn by `recipes/` on synthetic data: a KM curve (`km`), a
  dumbbell plot (`dumbbell`) and a patient timeline (`timeline`, loads its data through
  `pintu_sdk.cache`). Each adds a column or track past a set width.

```sh
uv run --project ../../backend pintu serve --project .     # then open ?board=recipes
```

Gallery: `scripts/make_gallery.py` writes 300 synthetic patient timelines to
`scratch/timelines/` with `pintu_sdk.save`, linked to `recipes.cohort:timeline`.
Filter them by patient, arm or stage in the gallery and drag one onto the board.
`timeline` is a `pintu_sdk.multiples` recipe: "Make multiples" in the inspector turns the
panel into a grid, and further timelines drop into its cells.

```sh
uv run --project ../../backend python scripts/make_gallery.py
```
