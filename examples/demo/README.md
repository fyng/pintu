# pintu demo project

Two boards:

- `demo`: synthetic plots (`scripts/make_plots.py`) placed as static files.
- `recipes`: recipe panels drawn by `recipes/` on synthetic data: a KM curve (`km`), a
  dumbbell plot (`dumbbell`) and a patient timeline (`timeline`, loads its data through
  `pintu_sdk.cache`). Each adds a column or track past a set width.

```sh
uv run --project ../../backend pintu serve --project .     # then open ?board=recipes
```
