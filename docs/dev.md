# Developer guide

## Layout

| Path | Contents |
|---|---|
| `backend/` | PyPI package `pintu`: board model, geometry, codegen, typst-py renderer, FastAPI server, CLI |
| `frontend/` | Vite + React app; `npm run build` writes it into `backend/src/pintu/static/` |
| `typst/board.typ` | Assembly library; shipped in the wheel as `pintu/typst/board.typ` |
| `examples/demo/` | Demo project with synthetic plots (`scripts/make_plots.py`) |

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

`source.recipe` and `source.multiples` are accepted and kept, and draw as placeholders.
Keys pintu does not know are kept as written.

## Tests

```sh
cd backend && uv run pytest              # geometry (incl. fig-span via typst), board, codegen, files, API
cd frontend && npm test                  # vitest: snapping and drop geometry
cd frontend && npm run build && npm run e2e   # Playwright smoke test and preview latency
```

The geometry test also checks against `../academic-design-system/formats/publication/fig.typ`
when that repo sits next to this one; otherwise it checks the vendored copy only.

`backend/scripts/latency.py <url> <board> <panel>` measures edit → preview latency against a
running server.
