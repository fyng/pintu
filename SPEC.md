# pintu: design spec (draft 6)

pintu (拼图, "puzzle") is a storyboard app for academic figures. It lays out panels
on a page grid, links each panel to the Python code that draws it, re-renders panels
at their new size when the layout changes, and lets the user ask an LLM to change a
plot from the board.

## 1. Need

### The figure workflow

Academic figures move through three stages, and the user loops back and forth between
them:

| Stage | The user | Design system | pintu |
|---|---|---|---|
| 1. Scratch plot | Plots data to look at it, often hundreds of plots (one per sample, cohort, patient) | Loose reference, for a cleaner scratch plot | Indexes scratch outputs in a gallery; the user browses, filters and picks from them |
| 2. Production plot | Turns a chosen plot into one that carries a message | Enforces standards; new plot forms go back into the design system | Hosts production plots as *recipes* (§8); re-renders them at the size the layout gives; lints them against the style pack; the LLM promotes a scratch script to a recipe |
| 3. Figure | Arranges production plots into panels that build a message in order, or refer to each other | Layout rules (grid, gutters, letters) | The storyboard: arrange, size, letter and assemble; resizing a panel sends it back to stage 2 by re-running its code |

pintu's main job is stage 3, together with the loop from stage 3 back to stage 2. It
touches stage 1 only through the gallery.

### Feature needs

| Need | Priority | Spec | Built in |
|---|---|---|---|
| Drag, click and drop storyboard for the composition and size of plots | Core | §6 | P1 |
| File-system access on Linux, Windows and macOS, to pick existing plots | Core | §4, §7 | P1 |
| Resizing a plot re-runs its code, so the plot uses the new space | Core | §8 | P2 |
| Prompt an LLM for a plot change from the storyboard | Advanced | §9 | P4, B3 |
| Opinionated file layout, so the board, the plots and the code stay in sync across hundreds of plots | Advanced | §7, §8 | P2, B1 |
| Layout templates | Future | §14 | B5 |

"Re-runs its code" covers three cases, in rising difficulty:

1. **Refit.** Same form, new margins and ticks. The recipe takes (w, h), so this
   comes free.
2. **Adapt by rule.** Past a set width, the recipe adds columns, tracks or entries,
   or switches form. The recipe author writes these branches on w and h.
3. **Adapt by meaning.** The new space suits a different plot, and no rule covers
   it. The LLM edits the recipe ("Adapt to size", §9).

## 2. Constraints

- **Users** can paste commands into a terminal and download an installer from
  GitHub Releases. App stores, code signing and one-click onboarding are out of
  scope.
- **Platforms:** desktop installers for Windows and macOS; a headless server for
  Linux and remote machines.
- **Python only** for plot code.
- **Independent repo.** pintu works without any design system. It reads external
  design systems and never writes to them (`docs/design-systems.md`). Journal
  presets, letters, fonts and lint rules come from style packs (§10). Users may
  bring their own plotting code; the LLM reads it like any other code.
- **Snap-only grid,** fine enough to feel smooth (§6).
- **Open LLM standard:** any OpenAI-compatible endpoint, including vLLM and SGLang
  (§9).
- **No automatic SSH tunnelling** until a security review clears it.
- **Open source:** pintu is MIT-licensed (`LICENSE` at the repo root; both PyPI
  packages and the installers ship under it). Every runtime dependency carries an
  OSI-approved licence compatible with MIT.

**Framework selection criteria**, in this order:

1. Speed, for the user: fast edits and fast renders.
2. Ease of maintenance: little custom code, and standard parts that coding agents
   know well.
3. Active community.
4. Ease of use.
5. Open-source licence (a hard filter).

## 3. Scope (v1)

**In:** everything in §1's table except templates.

**Out:**

- Plotting languages other than Python.
- Free placement off the grid.
- Store distribution, signed installers, auto-install of Python.
- Automatic SSH tunnelling.
- Offline first install.
- Authoring new design-system forms. pintu gives the style pack's rules to the LLM
  and to lint; the forms themselves are added in the design-system repo.

## 4. Architecture

```
┌ Desktop shell (Tauri 2) ─────────────────┐     ┌ Backend (Python, own env via uv) ───────┐
│ native window, file dialogs, updater     │     │ FastAPI + WebSocket                     │
│ spawns backend sidecar, picks free port  │────▶│ board model ⇄ board.yaml                │
│ ┌ Frontend (React + TS) ───────────────┐ │ WS  │ codegen → board.typ → typst-py → SVG/PDF│
│ │ canvas · gallery · inspector · chat  │◀┼─────│ catalog · file watch · lint             │
│ └──────────────────────────────────────┘ │     │ LLM agent loop · MCP server             │
└──────────────────────────────────────────┘     └──────────────┬──────────────────────────┘
                                                                │ Jupyter kernel protocol
                                                 ┌──────────────▼──────────────────────────┐
                                                 │ Recipe kernel (the user's Python env)   │
                                                 │ ipykernel · imports recipes · caches    │
                                                 │ data · renders SVG at cell size         │
                                                 └─────────────────────────────────────────┘
```

Three processes, two Python environments:

- **Backend** runs in an env that pintu owns, so its dependencies never clash with
  the user's.
- **Recipe kernel** runs in the user's env (conda, venv, uv, system), chosen per
  project. It keeps loaded data in memory across renders. `jupyter_client` gives
  start, interrupt, restart and output capture without custom IPC. The only
  requirement in the user's env is `ipykernel`.
- **Frontend** is a plain web app. The desktop shell wraps it; headless mode serves
  it to a browser.

**File access** goes through the backend's own file browser, which works the same on
every OS and over a remote connection. The desktop shell adds native open dialogs
for local projects.

### Run modes

| Mode | Who | How |
|---|---|---|
| Desktop | Windows/macOS users, local files | Install from GitHub Releases, open a project folder |
| Headless | Cluster or remote server | `uvx pintu serve --project .` on the remote; open the URL through an SSH or VS Code port forward |
| Desktop → remote | Laptop UI, cluster files | The desktop app connects to a headless backend by URL; the user opens the tunnel |

## 5. Stack

| Layer | Choice | Licence | Why | Rejected |
|---|---|---|---|---|
| Shell | Tauri 2 | MIT / Apache-2.0 | ~10 MB installers; sidecars; native dialogs; updater; `tauri-action` builds `.dmg`, `.msi`/`.exe` and AppImage on GitHub Actions | Electron: 100 MB+ per install, same features. pywebview + PyInstaller: weaker packaging |
| Backend bootstrap | Bundled `uv`; first launch runs `uv tool run pintu==<app version> serve` | MIT / Apache-2.0 | Backend stays a plain PyPI package: no freezing, one code path for desktop and headless | PyInstaller-frozen backend: brittle with native wheels; kept as the fallback if offline install is needed |
| Frontend | React 19, TypeScript, Vite, zustand | MIT | Largest ecosystem; coding agents write it well | Svelte 5: cleaner, fewer ready parts |
| Canvas | Custom SVG in mm with pointer events; `dnd-kit` for drops | MIT | The grid (§6) is plain rectangles; a layout library would add constraints and save little code | gridstack, react-grid-layout: other grid models. tldraw: licence not open source |
| Code view | CodeMirror 6 | MIT | Light, has a diff view | Monaco: heavy in a webview |
| Assembly | Typst via `typst-py` | Apache-2.0 | Compiles in milliseconds; vector PDF; embeds SVG, PDF, PNG | svgutils/pypdf: no text layout, weak fonts |
| Server | FastAPI, uvicorn, `watchfiles` | MIT, BSD-3, MIT | Standard, async, WebSocket | – |
| Kernel | `jupyter_client`, `ipykernel` | BSD-3 | Standard protocol, any env | Custom subprocess RPC |
| LLM | `openai` SDK with `base_url`; own agent loop | Apache-2.0 | One client for OpenAI, vLLM, SGLang, Ollama, LM Studio, OpenRouter | Vendor agent SDKs: one vendor. LiteLLM: large for what one client covers |
| Agent interop | MCP server (`mcp` SDK) | MIT | External agents (Claude Code, Codex) can drive the board | – |
| Catalog | SQLite | Public domain | No service to run | – |

## 6. Grid and layout

**Page.** Width and height in mm, a gutter `g` (default 3 mm), and `N` grid units
per axis (default `N = 36`, set per board).

**Grid lines.** Line `i` sits at `i · p`, with pitch `p = (L + g) / N` and `L` the
page width or height. A panel between lines `a` and `b` starts at `a · p` and is
`(b − a) · p − g` long. For any division `n` that divides `N`, this gives the same
cells as dividing `L` into `n` equal units with gutters `g` between them
(`even-span(L, n, i, k)`: the `k` units from unit `i`). `N = 36` covers halves,
thirds, quarters, sixths, ninths, twelfths and eighteenths, so thirds nested in thirds
land exactly. One unit is 5.2 mm at 183 mm. Fifths come from a multiples panel's
inner grid (§8).

**Panels** are rectangles `[x0, y0, x1, y1]` in grid lines. Panels do not overlap.
Edges snap to lines.

**Gestures**

- Click to select and open the inspector.
- Drag a panel body to move it; drag an edge or corner to resize.
- Drop a file or gallery item on empty cells to make a panel.
- Use "Split into n" for equal parts. The inspector warns when a split is not exact.
- Coarse guides (halves to sixths) show as strong lines. The fine grid shows while
  dragging.

**Letters** go in reading order (top to bottom, then left to right). The user can set
or clear any letter. A multiples panel takes one letter; a *group* (several panels
drawn by different functions) takes one letter on the group and none on its members.

- A group is an entry in the board's `groups:` list (§7). It counts as one unit in
  reading order, placed by the bounding box of its members' cells; its letter
  setting sits on the group, and a member's own `letter` key is ignored.
- Shift-click selects several panels; the inspector groups them, and ungroups a
  member's group. A panel is in at most one group; deleting a member down to one
  dissolves the group.

**Letter band.** A lettered panel reserves a full-width band at its top, and the
letter sits in it. The source fills the area below: a recipe renders at
`(w, h − band)`, a static file is fitted into it. A panel without a letter gets the
whole cell. The band is 3.5 mm by default (an 8 pt letter, 2.8 mm, plus a pad), set
by the style pack (`letter.band_mm`) or per board (`page.letter_band`), and at most
half the cell height. The area below the band is the panel's *plot area*. A static
file keeps its aspect ratio (`contain`, centred), so a file drawn at another aspect
leaves part of the plot area empty; the board view reports each static file's fit, and
a badge and the inspector flag a fit below 95% on either axis. On a group, the band runs along the top edge of the group's
bounding box: the members whose top edge is on it reserve the band, members below it
get none, and the letter sits on the leftmost member on that edge. When members do
not share a top edge, only the highest ones reserve the band.

## 7. Files and sync

A project is any folder. pintu adds:

```
<project>/
  pintu.toml                     # project settings: kernel env, style pack, gallery scan paths
  boards/<name>.board.yaml       # layout, the source of truth
  boards/build/<name>.typ, .pdf  # generated; never edit by hand
  pintu_out/<recipe>/<param-hash>/<w>x<h>.svg   # recipe renders
  pintu_out/<recipe>/<param-hash>/meta.json     # sidecar
  .pintu/                        # cache, catalog.sqlite, agent checkpoints; git-ignored
```

Recipes live wherever the user's code lives. The board names them by
`module:function`. Paths are relative to the project root and use `/` on every OS.

**Board file**

```yaml
version: 1
page: {width: 183, height: 170, grid: [36, 36], gutter: 3, style: nature}   # optional: letter_band (mm)
panels:
  - id: km
    cell: [0, 0, 36, 6]
    source:
      recipe: analysis.km.plot_km:km_one
      multiples: {item: cohort, mosaic: [[BRCA, LUAD, COAD, PRAD, PAAD]], share: {x: all, y: row}}
  - id: timeline
    letter: h
    cell: [18, 24, 36, 36]
    source: {file: outputs/timelines/P-0064072.pdf}   # static: no re-render
  - {id: dose, cell: [0, 24, 9, 36], source: {file: outputs/dose.pdf}}
  - {id: tox, cell: [9, 24, 18, 36], source: {file: outputs/tox.pdf}}
groups:                          # optional; members share one letter
  - {id: safety, panels: [dose, tox], letter: g}   # letter optional, as on a panel
```

`multiples` takes `item`, `mosaic`, and optionally `share` (default `{x: all, y: all}`),
`width_ratios` and `height_ratios`. A panel dropped from the gallery is named after
its recipe function and its item param value (`timeline-S002`), or the only param's
value.

**Sidecar**: `recipe`, `params`, `width_mm`, `height_mm`, `code_hash`, `script`,
`git_sha`, `created`. A file's sidecar is `<name>.meta.json` beside it, else `meta.json`
in its folder, which covers every output there.

- Scripts outside pintu write the per-file form with
  `pintu_sdk.save(fig, path, recipe=…, params=…)`; `code_hash` is computed as the
  render worker does. `script` is the root-relative path of the script that drew the
  file (default: the running `__main__` file, if under the root); "Promote to recipe"
  (§9) reads it.
- pintu's own renders write the folder form in `pintu_out/<recipe>/<param-hash>/`.
  Its size fields describe the newest render; each file's size comes from its
  `<w>x<h>` name.

A file with a sidecar that names a recipe is *linked*.

**Gallery.** Indexes image and PDF files under the scan paths (`[gallery] paths` in
`pintu.toml`, default the whole project; hidden folders and `boards/build` skipped)
into `.pintu/catalog.sqlite`. It groups linked outputs by recipe and filters them by
parameter (for example, 300 patients down to 3): values of one key OR, keys AND.
Linked outputs with the same recipe and params show once, as the newest file. The
gallery reads files; it does not require sidecars.

- Rescans are incremental by mtime and size of the file and its sidecar; `watchfiles`
  triggers them and pushes a `gallery` message to clients.
- Thumbnails are PNGs from typst-py (the first page of a PDF), cached in
  `.pintu/thumbs/`; the API pages items, and images load lazily.
- Dropping a linked item makes a `recipe` panel with its `params`, rendered at the
  cell size; an unlinked item makes a static `file` panel.

**Sync between board, plot and code**

| Direction | Trigger | Effect |
|---|---|---|
| Board → plot | Resize, param or mosaic edit | Re-render the panel (§8) |
| Code → plot | Recipe file saved, in pintu or any editor (`watchfiles`) | Re-render every panel that uses it |
| Plot → code | "Open code" on a panel | Open the recipe at its `def`, in the built-in view or the user's editor (`$EDITOR`, `code -g`) |
| Code → board | Recipe renamed or removed | The panel keeps its last render and shows a "missing recipe" badge |

## 8. Recipes

**Contract.** A recipe is a function that takes its render size in mm (the cell below
its letter band, §6) and returns a matplotlib `Figure` of that size:

```python
def timeline(w: float, h: float, patient: str) -> Figure: ...
```

A plain function works. The optional `pintu_sdk` (pure Python, no dependencies,
`pip install pintu-sdk`) adds metadata and caching:

```python
from pintu_sdk import panel, choice, cache

@panel(min_size=(40, 25), max_size=(183, 80), params={"patient": choice(list_patients)})
def timeline(w, h, patient):
    df = cache(load_timeline, patient)     # kept in kernel memory across resizes
    fig, ax = panel_figure(w, h)
    if w >= 120:
        draw_lab_tracks(ax, df)            # adapt by rule
    return fig
```

**Render path**, on drag end:

1. The canvas shows the old image scaled, with the new size.
2. The backend looks up `(code_hash, params, w, h)` in the cache.
3. On a miss, the kernel reloads the module if its hash changed (`code_hash` covers
   the project-local modules it imports; dependencies reload first), calls the recipe,
   and saves SVG with `svg.fonttype = "none"`, so text stays text.
4. Typst recompiles the board, and the frontend swaps the image in.

**Size range.** Outside `min_size`/`max_size`, the panel shows a badge and an
"Adapt to size" button (§9). pintu reads both from `@panel` with `ast` (literal tuples,
no import); the board view gives each recipe panel `sizeRange: {min, max, outside}`.
A resize that takes a panel outside its range starts an "Adapt to size" session.

**Multiples.** One lettered panel holds several sub-plots drawn by one function,
arranged from the board. The recipe draws one item on one axes:

```python
@multiples(item=choice(list_cohorts), min_cell=(25, 20))
def km_one(ax, cohort): ...
```

- **Mosaic.** The arrangement is a nested list of item values, in the form that
  matplotlib's `Figure.subplot_mosaic` takes.
  - A repeated value spans cells; its cells must form a rectangle.
  - `"."` leaves a cell empty.
  - `width_ratios` and `height_ratios` set uneven cells.
- **One render.** The SDK builds the panel at (w, h), with:
  - margins from the style pack (`style.margins`, passed to the kernel with each
    render),
  - axes shared per `share` (`all`, `row`, `col`, `none`, for x and y),
  - axis labels on the outer edges only: an axes keeps them if no axes sit directly
    below it (x) or left of it (y); shared tick labels hide the same way,
  - one shared key, from every axes' legend handles, above the grid.

  One render draws every sub-plot, so shared limits need no coordination. The
  render cache key and output hash include the mosaic, share, ratios and margins.
  Called without a mosaic, the recipe draws the item passed by name as 1 × 1, so
  scratch scripts and plain panels use the same function.
- **Editing.** The inspector shows the inner grid. The user sets rows × columns,
  drags items to reorder or span them, and drops gallery items into cells. Each edit
  rewrites `mosaic` in the board file.
  - A recipe panel whose recipe is `@multiples` offers "Make multiples": its item
    param becomes a 1 × 1 mosaic.
  - Dropping an item on another swaps them; on an empty cell, it moves there. The
    corner handle spans an item from its top-left to the target cell.
  - A linked gallery item fills a cell if its recipe is the panel's and it has the
    `item` param; dropped on the panel itself, it takes the first empty cell, or a
    new column.
- **Resize.** The mosaic stays as it is. Below `min_cell`, the panel offers a reflow
  (for example 1 × 5 → 2 × 3), which the user accepts or rejects.
  - The check uses the mean axes size the SDK's layout gives at the render size.
  - The offer lays items out one cell each in reading order, on the grid with room
    for all whose cells meet `min_cell` with the fewest empty cells (ties: aspect
    closest to `min_cell`'s); if none meets it, the grid closest to it. No offer if
    that is no better than the current grid. Rejecting hides it until the mosaic or
    size changes.
  - pintu reads `min_cell` and the item param from the decorator with `ast`, with no
    import.
- **Without the SDK,** a recipe that accepts `mosaic` and `share` works the same:
  it is called as `fn(w, h, mosaic=…, share={x, y}, **params)`, plus the ratios if
  set.

**Lint**, on each render and cache hit, from the worker's render summary (no
extra render or compile):

- `size`: the output size matches the render size to within `[lint] size_tol_mm`
  (0.1 mm).
- `overflow`: no text falls outside the figure.
- `font`: every drawn text is within `[lint] text_pt` (5–7 pt by default), and
  each font matplotlib set the text in is one Typst can find.

Style packs add rules as bounds on named render properties (`[[lint.rules]]`,
§10): `text_pt`, `tick_label_pt`, `tick_length_pt`, `tick_width_pt`,
`line_width_pt`, `spine_width_pt`. Each rule gives one issue per render, with the
worst value. Issues (`{rule, message}`) reach the board API (`render.lint`), a
lint badge on the panel and a list in the inspector, and the agent's
`render_panel` result and context. The pack's first font missing in Typst or in
the recipe env is a board warning and goes into the agent context.

**Recipe habits** (documented, not enforced): keep module top-level code light; put
slow loading behind `cache`.

## 9. LLM agent

**Endpoint config** (`~/.config/pintu/llm.toml`, one or more profiles):

```toml
[profiles.local]
base_url = "http://gpu-node:8000/v1"      # vLLM or SGLang
model = "Qwen/Qwen3-VL-32B-Instruct"
api_key_env = "VLLM_API_KEY"
vision = true
tools = true                              # native tool calling
```

The protocol is Chat Completions with `tools` and image content parts.

- vLLM needs `--enable-auto-tool-choice --tool-call-parser <parser>`.
- SGLang needs `--tool-call-parser`.
- An endpoint without native tool calling sets `tools = false`, which switches to
  JSON in the text.
- `provider = "copilot"` uses the user's GitHub Copilot subscription:
  `pintu login copilot` (GitHub device flow, with the user's own OAuth app client
  id) stores the GitHub token in `~/.config/pintu/` (0600); pintu then sets the
  auth and Copilot headers per request on `api.githubcopilot.com`. `pintu models
  copilot` lists the models with tool and vision support. No proxy process.

**Entry points**

| Action | Starts from | Does |
|---|---|---|
| Prompt | Chat panel, or a panel's menu | Any change to a plot. A prompt about a static-file panel first turns the file into a recipe (below) |
| Adapt to size | The size badge (§8), or a resize outside the size range | Edits the recipe to use the new size; adds a size rule rather than hard-coding one size; widens the `@panel` range |
| Promote to recipe | A gallery item whose script is known (sidecar `script`, else the module of its `recipe`, else a project `.py` file that names the file) | Writes a new module `recipes/<name>.py` with a `@panel` recipe that takes (w, h) and draws that item's plot; the script is left as is |

Each entry point starts a session (below); `pintu agent` and `pintu adapt` run one from
the command line, and `pintu accept` / `pintu revert` end it.

**Ownership.** The user owns the layout: cells, and so panel sizes. The agent owns the
plotting code. No session is offered `set_cell`; the agent may call only the tools it
is offered, and file writes under `boards/` are refused. A prompt about the size or the
space of a panel therefore changes its plot, never its cell. Figure-level layout
changes by the agent are a wishlist item (`docs/wishlist.md`).

**Loop:** build the context, call the model, run its tool calls, and return the
results. Repeat until the model stops or reaches the step cap (default 20; 30 for a
session about a static-file panel).

| Tool | Does |
|---|---|
| `read_file`, `list_dir`, `grep` | Read project code, confined to the project root |
| `edit_file` | Exact string replacement |
| `render_panel(id, w?, h?)` | A recipe panel: render through the kernel; return lint results, and the image if `vision`. A static-file panel: its fit into the plot area, and the file as placed if `vision` |
| `get_board` | Read the layout: page, pitch, letter band; per panel its letter, cell in grid lines and mm, band, plot area, source and a static file's fit |
| `run_python(code)` | Run in the recipe kernel; off by default, turned on per project (not built) |
| `write_file`, `render_recipe(recipe, w, h)` | Promote sessions and sessions about a static-file panel: create a new file; render a recipe not on a board |
| `use_recipe(recipe, params?)` | Sessions about a static-file panel: show a recipe in that panel instead of its file, in the same cell; refused if the recipe fails at the plot area |

**Context sent with each prompt** about a panel:

- the panel's cell (grid lines and mm), letter band and plot area, and that the cell
  stays as it is;
- a recipe panel: its recipe source, old and new size, the lint report, and the
  render (the image for vision models, a text summary of axes, text boxes and
  overflow for text models);
- a static-file panel: the file's size and its fit into the plot area, the image of
  the file as placed (vision models; the plot area's edge dashed), the script that
  drew it (sidecar `script`, else a project `.py` file that names the file) with its
  source, and the steps to a recipe: write it, check it with `render_recipe`, show it
  with `use_recipe`, then make the change;
- the style pack's rules.

A prompt about no panel gets the task and the board as `get_board` returns it.

**Safety and undo**

- Each agent turn starts with a checkpoint: a commit on a hidden git ref, or a file
  copy in `.pintu/` outside git.
  - In git, the tracked working tree is committed with a temporary index onto
    `refs/pintu/checkpoints/<session>/<turn>`; the user's index, HEAD and branches are
    untouched. The content of each file the agent writes, just before its first write
    in the turn and after each write, is kept as blobs on the same ref (untracked
    files too). Outside git, the same contents are copies in
    `.pintu/checkpoints/<session>/<turn>/`. No clean tree is needed.
  - Revert restores only the files the agent wrote, newest turn first, to their
    content before the turn; a file created by the agent is deleted. A file changed
    since the agent's last write to it (the user's edit, another session, a layout
    change in the board file) is a conflict: the revert changes nothing and reports
    it, unless told to skip or overwrite such files. Accept drops the checkpoint.
- The chat panel shows the diff (a `patch` part per turn), with Accept and Revert.
- Edits outside the project root, inside `.git/` and under `boards/` are refused.

**MCP server.** Exposes the same operations (`get_board`, `render_panel`, `lint`;
`set_cell` only with `pintu serve --mcp-allow-layout` or `pintu mcp --allow-layout`),
plus read-only `pintu://boards` resources, so terminal
agents can drive an open board: over streamable HTTP at `/mcp` on the running
`pintu serve` (acts on the open board), or over stdio with `pintu mcp --project .`
(its own headless board state; board edits reach a running server through its file
watch). The `mcp` Python SDK (MIT) serves both.

**Harness choice.** pintu keeps its own loop. opencode (MIT) was assessed as an
embedded harness on 2026-09-29 and not adopted:

- it always sends tools, so the `tools = false` fallback breaks (issue #35432);
- file undo needs a git repo;
- its v2 server API is marked experimental and ships near-daily;
- it adds a ~185 MB Bun binary beside Python; Windows support leans on WSL;
- its UI is SolidJS, so the React chat panel is built anyway.

pintu borrows its API shape for B3: sessions with typed message parts (text,
reasoning, tool call with state, patch), a session status map (idle, busy,
retry), abort, child sessions, and a staged revert. One session runs per panel;
several run at once. The chat panel lists running sessions with live progress,
stop and trace. A resize outside the size range starts a templated "Adapt to
size" session. External agents, opencode among them, drive the board through
the MCP server. Revisit embedding once opencode's v2 API is stable and #35432 is
fixed.

**Sessions as built** (`pintu/sessions.py`; the contract is `docs/api-sessions.md`):

- A session is one agent conversation, optionally bound to a board panel; each
  prompt is a turn, and a later prompt continues the same model history.
- Parts: `text` (user or assistant), `reasoning`, `tool` (state pending, running,
  done or error, with its output) and `patch` (the turn's diff per file).
- Status map: idle, busy, or retry (a transient model error: connection, timeout,
  429 or 5xx; up to three retries).
- Abort cancels the running model call or tool (a kernel render is interrupted, a
  subprocess render killed); unanswered tool calls get an "aborted" result, so the
  history stays valid.
- Child sessions carry a `parentId`; aborting a parent aborts its children. One
  top-level session may be busy per panel; sessions on other panels run at once.
- Staged revert undoes the turns from a given one on.
- REST under `/api/sessions`; live progress on the board WebSocket (`session.created`,
  `session.updated`, `session.status`, `part.added`, `part.updated`).
- Sessions persist in `.pintu/sessions/<id>.json`; after a restart a busy session
  comes back idle, its turn closed as interrupted.

**Chat panel as built** (`frontend/src/sessions.ts`, `components/Chat.tsx`):

- An "Agent" tab in the left sidebar, which widens while it is open. It lists
  sessions, newest first, with children nested under their parent. Each row shows
  its status: idle, busy, or retry with the attempt and a countdown. An open session
  shows its turns and parts: reasoning and tool calls collapsed, tool input and
  output, and the patch.
- Session events live in their own store, apart from the board store, so part
  events do not re-render the canvas.
- Diffs are rendered from the unified diff in each `patch` part, with line numbers.
  The CodeMirror merge view is not used: it needs both full texts, and the API sends
  diffs. It would also add a dependency.
- Accept and Revert work per turn: Accept takes turns up to n, and "Revert from
  here" reverts turns n and later. A revert conflict lists the files and offers
  "keep those" (skip) or overwrite. Stop aborts the turn. Trace shows the raw
  session and parts. Tool images are not stored, so a tool part only notes that an
  image went to the model.
- Entry points:
  - the prompt box, bound to the selected panel, or the last selected one, unless
    unticked; a scope line above it names the panel and its plot area, or the whole board;
  - "Prompt…" in the inspector;
  - the size-range badge on recipe panels (bottom left, red when the panel is outside
    the range; a click adapts), and "Adapt to size" in the inspector;
  - a notice for Adapt sessions started by a resize;
  - "Promote to recipe" on a clicked gallery item whose script is known, then
    "Place on board" in the first free cell.
- Without an LLM profile (`GET /api/llm`), the panel says so and the entry points
  are disabled.

## 10. Style packs

A style pack is a folder with `stylepack.toml`. The schema, documented and
validated in `pintu/style.py` (errors name the file, table and key):

| Table | Holds |
|---|---|
| `[pack]` | `name`, `default_preset`; optional `schema` (default 1; a newer schema is refused), `source = {url, ref}` (the guide and commit or tag the pack follows), `notes` (extra rules for the LLM; `pintu stylepack check` warns above 4 KB, a pack above 16 KB is invalid) |
| `[fonts]` | `family` (preference order), `paths` (font folders beside the file), `size_pt` |
| `[letter]` | `size_pt`, `weight`, `case` (`lower`, `upper`, `keep`), `band_mm`; optional `color` (hex) and `font` |
| `[margins]` | margins in mm for helpers such as multiples (`style.margins(preset, w, h)`): `left`, `right`, `top`, `bottom`, `gap`, `tick`, `title`, `key`; defaults match `pintu_sdk.multiples.MARGINS`. Optional `[margins.scale]`: `ref_mm = [w, h]`, `exponent` (0.5), `discount` (1), `fixed` (mm per margin that does not scale); at a w × h panel each margin m becomes f + (m − f)·k, k = 1 + discount·((w·h / ref area)^exponent − 1). Without it margins are fixed |
| `[presets.<name>]` | `widths`, `max_height`; optional `letter`, `margins`, `fonts`, `lint` and `matplotlib` overrides, merged per key (lint rules by `id`) |
| `[lint]` | `text_pt = [min, max]`, `size_tol_mm`, `[[lint.rules]]` (§8) |
| `[matplotlib]` | `rc`: flat rcParams (quoted dotted keys; scalars or arrays) the recipe kernel applies around each render and then restores; recipes need no import. Keys are not checked in the backend; an unknown key fails the render |
| `[typst]` | `snippet`, inserted after the board's page setup |

pintu ships a neutral `default` pack in the package; it holds the Nature preset
the prototype hard-coded. `[style] pack` in `pintu.toml` picks a pack by built-in
name or by a path relative to the project; `page.style` picks a preset in it (an
unknown name gives the default preset). Typst compiles with the font folders of
the pack and its presets, and the recipe kernel registers the preset's folders
with matplotlib. Lint, the agent's rules text (§9) and the render's rc use the
board preset's merged values. `pintu stylepack check <dir>` validates a pack
and prints its presets, warnings and rules text. A contract fixture
(`backend/tests/fixtures/stylepacks/contract/`) uses every key. `examples/stylepacks/strict/` mirrors a design
system's rules (5–6 pt tick labels, 1–3 pt ticks, 0.25–1 pt axes).
**Design systems.** pintu ships no pack for any design system. A user drafts a
pack from one into their own data folder; `docs/design-systems.md` §8.

## 11. Distribution

| Artifact | Built by | Notes |
|---|---|---|
| macOS `.dmg` (universal) | GitHub Actions, `tauri-action`, on tag | Unsigned; the README gives `xattr -dr com.apple.quarantine /Applications/pintu.app` |
| Windows `.msi`, `.exe` | same | Unsigned; SmartScreen "More info → Run anyway"; WebView2 ships with Windows 10/11 |
| Linux AppImage | same | – |
| `pintu` on PyPI | Trusted publishing, same tag | Backend and headless mode; the app pins its own version |
| `pintu-sdk` on PyPI | same | Optional recipe helpers |

**First run:**

1. Pick a project folder.
2. Pick a Python interpreter: detected conda envs, `.venv`, pyenv, or a pasted path.
3. If `ipykernel` is missing, the app shows the `pip install` command to paste.

The first launch needs internet: uv fetches the backend and Python.

## 12. Build process

The build runs in two phases:

1. **Prototype.** Proves the four riskiest assumptions, cheapest first, with
   everything else cut.
2. **Build.** Turns the prototype into v1.

A review gate sits between the two.

### 12.1 Prototype

| Step | Proves | Delivers | Exit test |
|---|---|---|---|
| P1 Layout loop | Snap-grid editing feels smooth; Typst preview is fast enough; the grid matches the even division | Headless backend, browser canvas, board YAML, Typst preview, static panels, file browser | Rebuild the OncoTraj Fig. 1–3 wireframe layouts from static PDFs; preview updates in < 300 ms; the geometry test matches the even division to 0.01 mm |
| P2 Resize loop | Resize → re-render is fast and reliable in a real env; code and board stay in sync | Kernel runner, plain-function recipes, render cache, file watch, "Open code" | Port 3 OncoTraj plots (KM, dumbbell, timeline) as recipes; warm re-render < 2 s; saving a recipe in VS Code re-renders the board in < 3 s |
| P3 Packaging spike (parallel with P2) | Users can install the app | Tauri shell, uv bootstrap, unsigned CI builds | A tester installs P1 on clean Windows 11 and macOS (arm64) machines from the README alone, in < 10 min |
| P4 Agent spike | Adapt by meaning works through an open endpoint | Minimal loop with read, edit and render tools; no chat UI (a CLI command or a panel button) | 3 of 5 set tasks (for example: double the width → add a column; halve the width → reflow) give an accepted diff on a vLLM model and on one hosted model |

**Cut from the prototype:** gallery and catalog, multiples, lint, style packs (a
Nature preset is hard-coded), SDK decorators other than `cache`, chat UI, checkpoints
(a clean git tree is required), Linux AppImage, updater.

**Gate.** Review the four results with the user. A failed step changes the stack
before the build starts:

| Failure | Fallback |
|---|---|
| P1: preview too slow | Typst in the browser via WASM |
| P2: kernel unreliable | Subprocess per render |
| P3: WebKit problems, or uv bootstrap fails | Electron, or a PyInstaller backend |
| P4: local model fails | Recommend hosted models; keep local as best effort |

### 12.2 Build

| Step | Delivers | Exit test |
|---|---|---|
| B1 | Gallery and catalog, sidecars, parameter filters, multiples panels, groups | Browse 300 patient timelines; drag 3 into one multiples panel and reorder them |
| B2 | Lint, style packs | Design-system rules flag a panel with 4 pt ticks |
| B3 | Chat panel with session manager (list, status, stop, trace; §9 "Harness choice"), checkpoints, diff view, Promote to recipe, MCP server | A full stage 1 → 3 loop on one figure without leaving pintu |
| B4 | Release hardening: first-run wizard, updater, docs, CI screenshot tests on WebKit and WebView2 | v1.0 tagged; a new user builds a figure from the example project |
| B5 | Layout templates | – |

### 12.3 Repo and dev loop

```
pintu/
  backend/    # PyPI package `pintu`: server, board model, codegen, kernel, agent
  sdk/        # PyPI package `pintu-sdk`
  frontend/   # Vite app; the build is copied into the backend package
  shell/      # Tauri (src-tauri)
  typst/      # board.typ, the assembly library
  examples/   # demo project with public data and recipes; not OncoTraj
  docs/
  LICENSE     # MIT
```

- **Dev:** `uv run pintu serve --dev` and `npm run dev` (Vite proxies to the
  backend).
- **Tests:**
  - pytest: geometry, codegen, and the kernel against the example recipes
  - vitest: snapping
  - Playwright: end to end, headless on Linux CI
  - the `tauri-action` build matrix
- **Reference projects:**
  - OncoTraj Figs. 1–3, for real-world use
  - `examples/`, to check that pintu works without OncoTraj or the design system

### 12.4 Speed budgets

| Action | Budget |
|---|---|
| Drag and resize on the canvas | 60 fps; no server call during a drag |
| Board preview after a layout edit | < 300 ms |
| Panel re-render, warm cache | < 2 s |
| Gallery thumbnail grid, 300 items | First screen < 1 s; the rest load lazily |
| App start to usable board, after the first run | < 5 s |

### 12.5 Status (2026-09-29)

Branch `prototype` holds P1, P2 and the P4 spike (merged from `p4`).

**Gate (2026-09-29):** P1 and P2 accepted. P3 deferred; the build starts
without it, and P3 must pass before B4. P4 closed on the local model alone;
the hosted-model half moves to §14 (OpenRouter). No stack change.

**Prototype steps**

- [x] P1 Layout loop. Fig. 1–3 wireframes rebuilt (23 panels, error < 1e-13 mm);
  geometry matches the even division to 0.01 mm for every n dividing 36; preview median
  30–70 ms, max 362 ms (1 of 90 edits over budget, on the network filesystem).
  Accepted: the 300 ms budget is advisory.
- [x] P2 Resize loop. Kernel runner, render cache, recipe watch, "Open code",
  `pintu-sdk` with `cache`. Warm resize median 209–387 ms; recipe save to board
  re-rendered median 808 ms. Measured with a scripted file write, not VS Code;
  accepted as is.
- [ ] P3 Packaging spike. Deferred at the gate; no Rust toolchain on the dev
  host. Must pass before B4.
- [x] P4 Agent spike. Closed on the local model (GLM-5.3-Flash); hosted models
  are best effort until the OpenRouter eval (§14).
  - [x] LLM client and profiles, agent loop, tools (without `run_python`),
    `pintu agent` and `pintu adapt` commands, eval harness with 5 tasks.
  - [x] Eval on GLM-5.3-Flash (local vLLM), `tools = true`: 5 of 5 automatic
    passes.
  - [x] Human acceptance of the 5 diffs: 4 of 5 accepted as is (tasks 1–4);
    task 5 accepted only without its ad hoc y-limit block. Tasks 2, 4 and 5 each
    added their own letter-zone clearance; it belongs in the shared panel
    helper or pintu's panel margins. Tasks took 2–25 min each.
  - [x] Eval with `tools = false`: 5 of 5 on the kernel path (below). The first run scored 0 of 5,
    because vLLM's glm47 parser strips `<tool_call>` text even when no tools are
    sent; the text protocol now uses fenced `tool_call_json` blocks. In the rerun,
    task 1 passed, task 2 stopped at step 1 on a reply that announced a call but
    held none, and tasks 3–5 hit a stopped server (503). Fixed without a live
    repro: text mode also takes server-parsed `tool_calls` and GLM's
    `<arg_key>` form, and a reply with no call before any edit gets one reminder.
  - [ ] Eval on a hosted model. GitHub Copilot provider built and logged in with
    the user's own GitHub App. The login lists only gpt-4o, gpt-4o-mini,
    gpt-41-copilot and gpt-3.5, and every image part is rejected ("image media
    type not supported"). gpt-4o with `vision = false`: 5 of 5 automatic passes
    in 10–27 s per task, but 2 of 5 accepted by review (tasks 2 and 5). Task 4
    swapped the axis labels and tasks 1 and 3 barely adapted or overlapped the
    data; the automatic checks missed all three. Deferred: rerun on a hosted
    model with vision through OpenRouter (§14).
  - [x] Merge into `prototype`; `render_panel` renders through the kernel
    (`Renders.render`, the board's path); the CLI builds its own
    (`agent.local_renders`).
  - [x] Rerun the eval on the kernel path, `tools = true` and `false`. First
    run: tools 4 of 5 (task 3 hit the step cap on repeated `edit_file`
    old_string misses); text 4 of 5 (task 2 sent an edit fence missing its last
    `}`, parsed as no call, ended "done" unchanged). Fixes: a call missing only
    its final closers is repaired, any other broken call returns an error
    result; an `edit_file` miss returns the closest region with line numbers
    and how it differs, a multiple match lists its lines. Rerun: tools 5 of 5
    (4–17 steps), text 5 of 5 (3–11 steps), all stopped "done". Neither new
    message appears in the rerun transcripts, so the pass does not prove them.

- [x] Letter band (§6): a lettered panel renders its source below a 3.5 mm band,
  so recipes need no letter-zone clearance; the letter-zone lint and prompt rule
  are gone. Preview median 25 ms, max 57 ms on the demo boards.

**Build steps**

- [x] B1 Gallery, catalog, sidecars, filters, multiples, groups.
  - [x] `pintu_sdk.save` and sidecars; SQLite catalog with incremental and live
    rescans; typst-py PNG thumbnails; gallery API (groups, facets, filters, pages);
    gallery panel with drag onto the board; `examples/demo/scripts/make_gallery.py`
    writes 300 linked timelines (`recipes/cohort.py`).
  - [x] Gallery budget on the 300 items, first page (60 items) plus 24 thumbnails
    through the API: warm 61–97 ms, cold (no catalog, no thumbnails) 181 ms on local
    disk and 503 ms on the network filesystem. In the browser (Playwright), picking
    the recipe to every visible thumbnail decoded: 157–294 ms, cold thumbnails; one
    run at 2.9 s, beside the other e2e specs rendering recipes in parallel.
  - [x] Multiples panels: `pintu_sdk.multiples` and `choice`; mosaic, share,
    outer-edge labels, one key; inspector inner grid with dnd-kit (rows × columns,
    move, swap, span, gallery drops into cells); reflow offer below `min_cell`;
    demo `recipes.cohort:timeline` is a multiples recipe.
  - [x] Groups: `groups:` in the board file, one letter and one band per group,
    shift-click to group, ungroup in the inspector.
  - [x] Gallery-dropped panels get readable ids (`timeline-S002`), not `panel`.
  - [x] Exit test (Playwright `e2e/multiples.spec.ts`): pick the 300 timelines,
    filter to 3, drop one on the board, make it a multiples panel, drag the other two
    into its 1 × 3 grid, swap the first and last; the board file reads
    `mosaic: [[S009, S005, S002]]` and the render's sidecar and SVG follow.
- [x] B2 Lint and style packs (branch `b2-stylepacks`). Exit test passes with
  `examples/stylepacks/strict/`: a recipe with 4 pt tick labels is flagged
  (`tick-labels`, `font`), one with 4 pt tick marks (`tick-length`); a clean
  recipe passes. Preview latency on the demo boards unchanged (median 30–39 ms
  against 40–44 ms before, same host).
- [x] B3 Chat panel, sessions, checkpoints, Promote to recipe, MCP server.
  - [x] Backend: session manager (parts, status, abort, children, persistence),
    checkpoints on a hidden git ref or `.pintu/` copies (P4's clean-tree rule is
    gone), diff, accept, revert and staged revert with conflict detection, the three
    entry points, `@panel(min_size, max_size)` in `pintu-sdk` and `sizeRange` in the
    board view, auto-started Adapt to size, sidecar `script`, MCP server (`mcp` 2.2,
    HTTP at `/mcp` and `pintu mcp` stdio). API contract: `docs/api-sessions.md`.
  - [x] Backend validation on GLM-5.3-Flash, `tools = true`: the P4 eval on
    sessions, 5 of 5 automatic passes (4–8 steps, 73–285 s), session diffs cover the
    same files as `git diff`; `scripts/stage_loop.py` on the demo passes (promote
    `plots/lines.pdf` from `scripts/make_plots.py` in 11 steps, accept, place,
    resize to full width, an Adapt to size session starts and edits the recipe in
    6 steps, revert restores the promoted recipe and keeps the layout); an `mcp`
    client drives the four tools over HTTP and stdio. The promoted recipe declared
    a one-size range (min = max), so the prompt now asks for a range.
  - [x] Chat panel (§9 "Chat panel as built"): session list with
    status and children, live parts, stop, trace, diff with per-turn Accept and
    staged Revert with conflicts, size badge and Adapt to size, Promote to recipe
    and place, and `GET /api/llm` for the no-profile message. Tests: vitest for the
    session store (events, part merge, status, turns, conflicts). Playwright
    `e2e/chat.spec.ts` and `e2e/exit.spec.ts` run on a scripted
    OpenAI-compatible endpoint (`e2e/fake-llm.mjs`), after the other specs.
  - [x] Exit test in the browser on GLM-5.3-Flash (`e2e/exit.spec.ts` with
    `PINTU_E2E_PROFILE=glm`), 3.4 min in all:
    - pick `plots/lines.pdf` in the gallery and promote it (12 steps, 86 s);
    - accept, place and render the recipe;
    - widen the panel to 183 mm; an Adapt to size session starts (8 steps, 111 s),
      adds a `w >= 140` rule and widens the range;
    - accept.

    A first run stalled after the placement: the page kept a stale render badge
    while the server showed the render ok. The rerun passed, and the stall has not
    been reproduced.
- [x] Style pack schema, phase A: `[pack] schema` and `source`; `pintu stylepack
  check` and the notes cap; per-preset `fonts`, `lint` and `matplotlib`
  overrides; `letter.color` and `font`; `[matplotlib] rc` in the kernel;
  `[margins.scale]`. The default pack's board Typst, compiled SVG and PDF, render
  cache keys and output paths are unchanged; preview latency unchanged (median
  28–32 ms both before and after).
- [x] C1 Cleanup (`docs/design-systems.md` §11): no test, code or doc depends on a
  design system checkout.
- [ ] D1–D7 Design systems (`docs/design-systems.md` §10).

**Known bugs and gaps**

- "Open in editor" via `$EDITOR` does nothing useful for terminal editors or a
  remote backend; the built-in view is the fallback.
- The first render after a kernel interrupt sometimes takes 2–5 s; cause unknown.
- SVG text falls back to another font when the style pack's font is not
  installed (IBM Plex Sans is missing on the dev host); the board now warns. The
  default pack ships no font files.
- The demo timeline recipe draws a 7.2 pt label, which lint flags.
- Preview tail latency on the network filesystem reaches 362 ms; file-existence
  checks in codegen are the likely cause, not yet measured.
- Not built: undo, typed cell entry, board rename and delete.
- `e2e/gallery.spec.ts` and `e2e/multiples.spec.ts` race, since both drop timelines
  and renders join the catalog; most runs fail one of them, before B3 too.

**Dev setup notes**

- Node 22 comes from the conda env `pintu-node`; the system Node (v10) is too
  old.
- The tracked repo must not name the reference project; its ported recipes and
  exit-test boards live in git-ignored `local/`.

## 13. Risks

- **WebKit (macOS) and WebView2 (Windows) render differently.** P3 tests it; B4
  adds CI screenshots; Electron is the fallback.
- **Unsigned installers put some users off.** Accepted; signing costs money and
  upkeep.
- **Small local models edit code poorly.** P4 measures it. Document tested models;
  put every edit behind a checkpoint and a diff.
- **Recipes with heavy import-time work re-render slowly.** Handled by `cache`, the
  kernel's memory and documented habits.
- **Fonts differ between matplotlib and Typst.** Typst sets SVG text from the style
  pack's font path; lint flags missing fonts.

## 14. Future

- Hosted-model eval through an OpenRouter profile (a model with vision and tool
  calling), to finish the hosted half of the P4 exit test.
- Layout templates: a board with empty slots, filled from the gallery.
- Automatic SSH tunnelling, after a security review.
- Plotting languages other than Python, through a subprocess recipe contract.
- The feature wishlist, with figure-level layout optimization by the agent, is
  `docs/wishlist.md`.

## 15. Open questions

- This spec names the reference project, while the tracked repo must not. Reword
  the spec, or keep it as the one exception?
- P4 has only a local endpoint. Which hosted model runs the second half of the
  P4 exit test?
