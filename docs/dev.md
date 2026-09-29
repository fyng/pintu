# Developer guide

## Layout

| Path | Contents |
|---|---|
| `backend/` | PyPI package `pintu`: board model, geometry, codegen, typst-py renderer, recipe kernel and render cache, FastAPI server, CLI |
| `sdk/` | PyPI package `pintu-sdk`: optional recipe helpers (`cache`); pure Python |
| `frontend/` | Vite + React app; `npm run build` writes it into `backend/src/pintu/static/` |
| `typst/board.typ` | Assembly library; shipped in the wheel as `pintu/typst/board.typ` |
| `examples/demo/` | Demo project: static plots (`scripts/make_plots.py`, board `demo`) and recipes (`recipes/`, board `recipes`) |

## Setup

Needs [uv](https://docs.astral.sh/uv/) and Node 20+.

```sh
cd backend && uv sync                # Python env with dev tools
cd ../frontend && npm install
npx playwright install chromium      # only for the end-to-end test
```

## Run

Built frontend, served by the backend:

```sh
cd frontend && npm run build
cd ../backend && uv run pintu serve --project ../examples/demo    # http://127.0.0.1:8765/
```

Dev loop, with hot reload for the frontend (Vite proxies `/api` to the backend):

```sh
cd backend && uv run pintu serve --project ../examples/demo --dev
cd frontend && npm run dev                                        # http://localhost:5173/
```

`PINTU_BACKEND` points the proxy at another backend URL.

## How an edit flows

1. The canvas snaps the drag locally; nothing goes to the server until pointer up.
2. The frontend posts the edit to `POST /api/boards/<name>/ops`.
3. The backend applies it to a copy of the board, validates it, and takes it as current.
4. It compiles the generated Typst source from memory to SVG and pushes it on `/api/ws`.
5. A writer thread then writes `boards/<name>.board.yaml`, `boards/build/<name>.typ` and `.pdf`.

External edits to a board file reload through `watchfiles` and push the same way.

## Recipes

A panel with `source: {recipe: module.path:function, params: {...}}` is drawn by calling
`function(w, h, **params)` at the cell size in mm; it returns a matplotlib `Figure`. The module
must live under the project root. Renders go to `pintu_out/<recipe>/<param-hash>/<w>x<h>.svg`
with a `meta.json` sidecar.

- **Kernel.** `KernelRunner` (`kernel.py`) starts an ipykernel with `[kernel] python` from
  `pintu.toml` (default: the backend's own interpreter), loads `worker.py` into it once as the
  `pintu_worker` module, and calls `pintu_worker.render` per render. Imported modules and data stay
  in memory; the recipe module reloads when its file hash changes (modules it imports do not).
  Renders run one at a time. A render past the timeout (120 s) interrupts the kernel; a dead
  kernel restarts on the next render. Without `ipykernel` in that env, the render error gives
  the `pip install` command.
- **Scheduling.** `Renders` (`renders.py`) runs on every publish, board load and Python file
  change: for each recipe panel it compares recipe, params, size and module hash with the last
  render. A cache hit (`cache.py`, index in `.pintu/renders.json`) is taken at once; a miss is
  queued, and the queue keeps only the latest request per panel. Until the new render lands, the
  panel shows its last render scaled to the new cell and a "rendering…" badge.
- **Watch.** `watchfiles` watches `*.py` under the project root (not `.pintu/`, `pintu_out/`,
  `boards/`); a save re-renders the panels whose module hash changed. Set
  `WATCHFILES_FORCE_POLLING=1` on file systems without inotify.
- **Missing recipe.** If the module file or the `def` is gone (checked with `ast`, no import), the
  panel keeps its last render and shows a "missing recipe" badge. Render errors show a red badge
  and the traceback in the inspector; the last render stays.
- **Open code.** `GET /api/recipes/locate?recipe=` returns file, line and source for the built-in
  CodeMirror view; `POST /api/recipes/open` runs `code -g file:line`, else `$VISUAL`/`$EDITOR
  +line file`, on the backend host.

Recipes may use `pintu_sdk.cache(fn, *args)` to keep slow loads in kernel memory across renders
and reloads; they must also run without it (see `examples/demo/recipes/timeline.py`).

The generated `.typ` uses root-relative paths; compile it by hand with
`typst compile --root <project> boards/build/<name>.typ`.

## Board file

```yaml
version: 1
page: {width: 183, height: 170, grid: [36, 36], gutter: 3, style: nature}
panels:
  - id: lines
    cell: [0, 0, 18, 18]          # grid lines x0, y0, x1, y1
    source: {file: plots/lines.pdf}
    letter: h                     # optional: a fixed letter; false for none; absent for reading order
```

`source.recipe` panels render as above (edit them with the `set_recipe` op or the inspector).
`source.multiples` is accepted and kept, and draws as a placeholder. Keys pintu does not know
are kept as written.

## Tests

```sh
cd backend && uv run pytest              # geometry (incl. fig-span via typst), board, codegen, files, API,
                                         # kernel runner, render cache, recipe watch, sdk: uv run pytest ../sdk/tests
cd frontend && npm test                  # vitest: snapping and drop geometry
cd frontend && npm run build && npm run e2e   # Playwright: layout edits, preview latency, recipe resize/save/open-code
```

The geometry test also checks against `../academic-design-system/formats/publication/fig.typ`
when that repo sits next to this one; otherwise it checks the vendored copy only.

`backend/scripts/latency.py <url> <board> <panel>` measures edit → preview latency against a
running server.
