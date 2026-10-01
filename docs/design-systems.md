# Design systems: loose coupling (draft 1)

pintu reads external design systems and never writes to them. A design system (DS) is
any folder or public git repo of figure guidance: chart forms, style sheets, palettes,
type scales, layout rules. `kare` is one; SciencePlots is another. pintu builds its own
index of each DS, shows the DS's elements in the gallery and the file browser, gives
them to the agent, and (later) ports chart forms into code templates kept on the
user's machine.

This spec replaces the built-in `kare` pack (SPEC §10, kare integration Phase B) and
the kare checks in the tests. §11 lists the cleanup.

## 1. Rules

1. **Read only.** pintu never writes to a DS: no commits, branches, pull requests or
   pintu files in it.
2. **The DS needs no knowledge of pintu.** pintu requires no manifest in the DS. It
   derives its index from the files the DS already has.
3. **No DS-specific code in pintu.** Readers are generic (front matter, Markdown,
   images, style sheets, token files). Nothing in the repo or tests names a DS.
4. **Derived things stay with the user.** The index, drafted style packs and code
   templates live in the user's pintu data folder (§2). A project holds only DS
   references and the copies the user takes.
5. **The style pack binds; a DS guides.** Lint and presets come from the one style
   pack (SPEC §10). DS docs inform the agent and the user. On a conflict the pack
   wins, then the DS listed first.
6. **Works without an LLM.** Without a profile, the index comes from the inventory
   alone (§4.3): plainer, still browsable.

## 2. Registry and storage

**User registry**, `~/.config/pintu/designs.toml`:

```toml
profile = "glm"                          # llm.toml profile for indexing; default: the llm.toml default

[systems.kare]
url = "https://github.com/fyng/kare"     # public git repo: pintu clones it read-only
ref = "main"                             # branch, tag or commit

[systems.lab]
path = "~/src/lab-style"                 # local folder: read in place
```

**Project selection**, in `pintu.toml`. Order sets precedence. An entry may carry its
own source, so a shared project works on a machine whose registry lacks the id:

```toml
[[design.systems]]
id = "kare"
url = "https://github.com/fyng/kare"     # optional when the user registry has the id
commit = "f394c9b"                       # optional pin; default: the registry ref

[[design.systems]]
id = "sciplots"
```

A project entry overrides the registry entry with the same id. A project without
`[design]` uses no DS.

**Data folder**, `~/.local/share/pintu/` (`$PINTU_DATA_DIR` overrides):

```
designs/<id>/repo/                 # git sources: partial clone (--filter=blob:none)
designs/<id>/<commit>/tree/        # read-only checkout of that commit (git sources)
designs/<id>/<commit>/inventory.json
designs/<id>/<commit>/index.json
designs/<id>/overrides.toml        # the user's edits to the index; survive rebuilds
designs/<id>/thumbs/
stylepacks/<name>/                 # user packs, e.g. drafted from a DS (§8)
templates/<id>/<element>/<target>/ # code templates (§9)
```

A path source has no checkout. Its commit is `git rev-parse HEAD` (with `-dirty` if
the tree has changes) when the folder is a git repo, else the inventory hash.

**CLI**

| Command | Does |
|---|---|
| `pintu design add <id> <url\|path> [--ref R]` | Adds to the registry, fetches, indexes |
| `pintu design list` | Systems, source, commit, index status and problems |
| `pintu design update [<id>]` | Fetches the ref; reindexes when the commit changed |
| `pintu design index <id> [--profile P] [--no-llm]` | Rebuilds the index of the current commit |
| `pintu design show <ref>` | Prints a system or an element (§3) |
| `pintu design remove <id>` | Drops the registry entry; keeps the data until `--purge` |

Only `add` and `update` use the network. `pintu serve` never fetches. Private repos are
out of scope for v1; clone one yourself and add it as a path.

## 3. Terms and references

| Term | Meaning |
|---|---|
| Inventory | pintu's deterministic list of the DS's files and their facts (§4.1) |
| Index | The DS's elements, topics and summary, as pintu understands them (§4.2) |
| Element | One thing a figure maker picks: a chart form, a style sheet, a palette, a type scale, a layout rule set, an icon or illustration set, a specimen |
| Topic | Where the DS covers one of pintu's concerns: `sizes`, `letters`, `type`, `colour`, `lines`, `margins`, `layout`, `legend`, `statistics`, `export`, `accessibility`, `usage` |
| Template | Code the user's env runs that draws an element (§9) |

References:

- Element: `<id>:<element>`, optionally `@<commit>`: `kare:form-03`,
  `sciplots:nature@b9b1695`.
- File: `design://<id>/<path>`: `design://kare/core/charts/forms/out/03-survival-curves.main.png`.

Paths inside a DS resolve by the rules of `Project.resolve`: relative, no `..`, no
escape through symlinks.

## 4. Index

### 4.1 Inventory

A walk of the DS's tracked files (`git ls-files`, else every file not hidden, not in
`node_modules` or `__pycache__`). Per file: path, size, content hash, and

- docs (`.md`, `.rst`, `.txt`): YAML front matter, headings (to level 3), lead
  sentence, image references;
- images (`.png`, `.jpg`, `.svg`, `.pdf`, `.gif`, `.webp`): kind and size;
- code and data: the first 240 characters, and the lines that name an image in the
  inventory, with the 10 lines above (this links `savefig("fig02c.jpg")` to the
  `style.context(["science", "nature"])` above it);
- `LICENSE*` and `COPYING*`: the SPDX id, matched by text. The licence never comes
  from the LLM.

**Candidates.** pintu fixes these elements before the LLM sees the inventory, with
ids it chooses:

- a doc with front matter: the front matter `id`, else the file stem;
- a style sheet (`.mplstyle`): the file stem;
- a numbered series (`name-1` … `name-23`): one element, `name`, with the files as
  variants.

The LLM fills in the fields of each candidate and may propose more elements (a
palette section in a colour guide). A proposed element takes the id
`<doc stem>` or `<doc stem>-<heading slug>`, set by pintu, and is marked `proposed`
until an override pins it.

The inventory hash covers every file hash. An unchanged hash reuses the index.

### 4.2 Mediation

One LLM call per DS commit turns the inventory and the candidates into the index. The call gets the DS's
top-level docs (`README`, `INDEX.md`, `AGENTS.md`, `CLAUDE.md`, 14 KB each at most) and
the inventory. It returns JSON under a schema, as structured output when the endpoint
supports `response_format` with `json_schema` (vLLM, SGLang), else as JSON in the text.

```json
{
  "summary": "…", "use": "git submodule at design-system/",
  "topics": [{"topic": "sizes", "path": "formats/publication/README.md", "heading": "Size"}],
  "elements": [{
    "id": "form-03", "kind": "chart", "title": "Survival curves",
    "family": "time", "native_family": "time", "jobs": ["time to event"],
    "doc": "core/charts/forms/03-survival-curves.md",
    "images": ["core/charts/forms/out/03-survival-curves.main.png"],
    "code": [], "summary": "…"}]
}
```

`kind` is one of `chart`, `style`, `palette`, `type`, `layout`, `icon`, `illustration`,
`specimen`, `guide`. `family` (charts only) is one of `comparison`, `distribution`,
`relationship`, `time`, `matrix`, `composition`, `embedding`, `spatial`, `flow`.
`native_family` keeps the DS's own category.

**Verification**, by pintu, after the call:

- every path must be in the inventory; a bad path is dropped and listed in
  `problems`;
- ids are unique; enum values are valid;
- an element with no doc, image or code is dropped.

pintu stores the index with its inputs: DS commit, inventory hash, model, prompt hash,
pintu version and the problems. Each element gets a hash of its doc, images and code,
so a later commit can tell which elements changed (§8, §9).

**Overrides.** `designs/<id>/overrides.toml` edits the index after each build:

```toml
[elements.form-17]
images = ["formats/publication/out/specimen-multitrack-timeline.png"]

[hide]
ids = ["specimen-website"]
```

### 4.3 Without an LLM

The candidates (§4.1) become the index. `kind` comes from the front matter (`guide`
if it is not one of pintu's; `style` for style sheets), the title from the first
heading or the file stem, images from the doc's image references and from code lines
that name images. `family` and topics stay empty. kare gives its 26 chart forms this
way; SciencePlots gives its 25 style sheets, with no families and no example images.

### 4.4 Cross-referencing

pintu owns the vocabulary (kinds, families, topics). Every DS maps onto it, so one
filter spans all systems: "family = time" lists `kare:form-03` and any other system's
survival plot. Search matches title, jobs, summary and native family across the active
systems and labels each hit with its system. pintu draws no element-to-element links
across systems in v1; shared facets do that work.

## 5. Gallery and file browser

**Gallery.** A source switch above the grid: *Project* (today's gallery) and *Design*.

- *Design* lists the elements of the project's active systems, grouped by system,
  then kind or family. Facets: system, kind, family, job. A text box searches.
- A card shows the element's first image, else its title. Thumbnails come from the
  same typst-py path as project thumbnails and go to `designs/<id>/thumbs/`.
- A click opens the element: title, summary, jobs, every image, the doc as read-only
  Markdown, `kare @ f394c9b`, "Open in Files", and the actions "Place as reference"
  (§6) and, later, "Make template" and "Use template" (§9).
- A system whose index has problems shows a badge with the list.

**File browser.** A root picker: *project* and each active system
(`design://kare/`). DS roots are read only. Image files drag onto the board as
reference panels.

**API**

| Route | Returns |
|---|---|
| `GET /api/designs` | Active systems: id, source, commit, status, problems |
| `GET /api/designs/items?system=&kind=&family=&job=&q=&offset=` | Element pages and facets |
| `GET /api/designs/{id}/elements/{el}` | One element, with its doc text |
| `GET /api/designs/{id}/raw?path=` | A DS file |
| `GET /api/designs/thumb?ref=` | A thumbnail |
| `GET /api/files?root=design:<id>&path=` | A DS folder listing |

## 6. Reference panels

A storyboard often starts as "a survival plot goes here". A reference panel holds a
DS image as a placeholder:

```yaml
- id: km
  cell: [0, 0, 18, 12]
  source: {design: "kare:form-03", image: main}    # or {design: "design://kare/…png"}
```

- It draws like a static file panel, with a "reference" badge.
- A prompt on it starts a static-panel session (SPEC §9) whose context adds the
  element's doc: write a recipe in that form with the project's data, check it with
  `render_recipe`, show it with `use_recipe`.
- On a machine without the system, the panel shows "kare not found" and the board
  stays valid.

## 7. Agent

**Context.** One section, at most 1.5 KB: per active system its id, commit, summary and
topics; then the precedence rule (§1.5). No DS docs go in by default.

**Tools**, read only, offered in every session when a system is active:

| Tool | Does |
|---|---|
| `design_search(query, system?, kind?, family?)` | Up to 10 hits: ref, title, kind, family, summary, doc. Plain text match over the index; no LLM call |
| `design_read(ref, heading?)` | A doc's text (16 KB at most; one section when `heading` is given); an image as an image part for vision profiles |

Write tools stay confined to the project, as today. DS trees sit outside the root, so
`edit_file` and `write_file` refuse them already.

## 8. Style packs from a design system

No DS pack ships with pintu. A user drafts one:

```sh
pintu stylepack draft kare --out kare-nature
```

- An agent session reads the system's topics (`sizes`, `letters`, `type`, `lines`,
  `margins`, `colour`) and writes `stylepacks/kare-nature/stylepack.toml` in the data
  folder, with `[pack] source = {design = "kare", url = "…", ref = "<commit>"}`.
  `pintu stylepack check` validates it; the user reviews it.
- `[style] pack` resolves a built-in name, then a user pack in the data folder, then a
  path relative to the project.
- `[fonts] paths` may name `design://kare/core/fonts/ibm-plex-sans`, so the pack
  references the DS's fonts and copies none.
- `pintu design update` warns when the commit moved and a topic's section changed:
  review or redraft the pack.

## 9. Code templates (future)

A template ports a DS chart form into code the user's env runs. The DS stays as it is;
pintu proposes no code to it.

- **Where:** `templates/<id>/<element>/<target>/`, in the user's data folder only.
  `<target>` is the framework and major version, e.g. `matplotlib-3`.
  - `template.py`: a recipe `def <name>(w, h, data=None, **params)` that draws demo
    data when `data` is None.
  - `template.toml`: source ref, commit and element hash; target; Python and framework
    versions it rendered with; model; pack it linted under; status (`draft`,
    `accepted`).
  - `previews/<w>x<h>.svg`.
- **Make template:** a session of kind `template`. Writes are confined to the template
  folder. Tools: `read_file`, `edit_file`, `write_file`, `design_read`, and
  `render_recipe` in a scratch kernel in the user's env. A vision model sees the DS
  image beside each render. Exit: renders at three sizes lint clean under the active
  pack.
- **Use template:** copies `template.py` into the project as `recipes/<name>.py`, with
  a header comment naming the template and DS ref. The project owns the copy from
  then on; normal sessions edit it. The render sidecar records `template`.
- **Stale:** when a new DS commit changes the element hash, the gallery marks its
  templates "design changed"; "Update template" starts a session with the doc diff.
- Targets coexist. A project offers the targets its kernel env can import.
- Sharing is copying the folder; export and import come later.

This keeps each user free to pick versions, frameworks and envs, while the DS spreads
as plain guidance with no code from pintu users colliding in it.

## 10. Phases

| Step | Delivers | Exit test |
|---|---|---|
| C1 | Cleanup (§11) | `pytest` passes with no DS checkout and no skips from it; `git grep -i -e kare -e academic-design` finds only this file |
| D1 | Registry, data folder, git and path sources, inventory, index without LLM, CLI | `pintu design add kare https://github.com/fyng/kare` and `add sciplots <url>`; `list` shows both commits; kare's index has 26 chart elements from front matter |
| D2 | Candidates, mediation, verification, overrides | On GLM-5.3-Flash, two runs each: 0 invalid paths; 26 of 26 kare forms with their native family; 25 of 25 SciencePlots styles; candidate ids equal across runs; image recall ≥ 0.9 on both |
| D3 | Gallery *Design* view, file browser roots, API | Playwright: filter family = time across two systems, open `kare:form-03`, browse `design://kare/` |
| D4 | Agent context and tools | A P4-style task, "draw survival curves as the design system says", calls `design_search` and `design_read` and lints clean |
| D5 | Reference panels | Drop `kare:form-03`, prompt on it, accept: the panel becomes a recipe panel in the same cell |
| D6 | Pack drafting | A drafted kare pack passes `pintu stylepack check` and flags 4 pt ticks |
| D7 | Templates (future) | One kare form as a `matplotlib-3` template; "Use template" in two projects |

C1 is independent and comes first. Status: C1 done (2026-10-01); D1–D7 not started.

## 11. Cleanup

| Item | Change |
|---|---|
| `backend/tests/conftest.py` | Remove `KARE_ENV`, `kare_dir`, `KARE_SKIP` |
| `backend/tests/test_geometry.py` | Delete `test_grid_matches_kare_fig_span`. The formula test and the vendored Typst test keep the coverage |
| `backend/tests/test_lint.py` | `test_pack_font_paths` builds its font at test time: copy matplotlib's DejaVu Sans and rename its family with fontTools (both ship with matplotlib, a dev dependency), so Typst and the kernel find it only through the pack path. No skip |
| `backend/src/pintu/geometry.py`, `typst/board.typ`, `frontend/src/geometry.test.ts` | Rename `fig_span` / `fig-span` to `even_span` / `even-span`; drop DS names from comments. Generated `.typ` files rebuild on the next preview |
| `examples/stylepacks/strict/stylepack.toml` | Drop the line naming a real DS pack |
| `docs/dev.md` | Drop the note on the DS checkout in the geometry test |
| `SPEC.md` | §2: "pintu works without any design system"; §6: state the even-division formula, no DS name; §10: replace the kare paragraph with a pointer here; §12: B2 "the `kare` pack" → "style packs"; status: replace "kare integration" with C1 and D1–D7 |
| `pintu-demo/` (beside the repo, not tracked) | Delete. It is `examples/demo` plus two scratch recipes (`heatmap.py`, `lines.py`) and a test board. `local/RUN.md` then copies `examples/demo` to a scratch folder and `git init`s it |
| `local/kare-integration-plan.md` (ignored) | Superseded by this file; delete |

## 12. Spike (2026-10-01)

A throwaway prototype of §4.1–4.2 and §4.4 (git-ignored `local/ds-index-spike/`), on
GLM-5.3-Flash (local vLLM, 4 × H200, structured output by JSON schema), against two
systems with different shapes:

- kare `f394c9b`: Markdown forms with front matter, rendered PNGs, Typst, JS kit.
- SciencePlots `b9b1695` (MIT): `.mplstyle` files, a script that saves example JPGs,
  no front matter.

The spike had no candidates step: the LLM chose every element and id. Ground truth
came from kare's front matter and from the `style.context` calls in SciencePlots'
example script.

| | kare run 1 | kare run 2 | SciencePlots run 1 | SciencePlots run 2 |
|---|---|---|---|---|
| Inventory | 127 files, 62 KB | same | 111 files, 41 KB | same |
| Prompt / completion tokens | 22.6k / 34.8k | 22.6k / 31.6k | 16.8k / 27.9k | 16.8k / 24.0k |
| Time | 395 s | 320 s | 322 s | 266 s |
| Elements | 41 | 42 | 25 | 47 |
| Invalid paths | 0 of 173 | 0 of 165 | 0 of 114 | 0 of 134 |
| Truth found | 26 of 26 forms, native family 26 of 26 | same | 25 of 25 styles | 25 of 25 |
| Image precision / recall | 1.00 / 1.00 | 1.00 / 1.00 | 0.93 / 0.95 | 0.91 / 0.46 |
| Topics | 12 | 12 | 8 | 8 |

Findings, and what they changed in this spec:

- Elements the DS declares (kare's front matter) came out the same in both runs: same
  ids, kinds and families.
- Elements the LLM had to invent did not. kare's 15 or 16 non-form elements differed
  by 8 ids between runs (`publication`, `charts` against `panel-contract`,
  `valence`). SciencePlots run 2 split `discrete-rainbow-1` … `-23` into 23
  elements, which dropped image recall to 0.46. Hence the candidates step (§4.1):
  pintu sets ids and groups numbered series; the LLM fills in fields.
- Neither run found SciencePlots' MIT licence: the crawler skipped `LICENSE`. Hence
  the deterministic licence (§4.1).
- Topic mapping was mostly right. kare's `letters` pointed at *The panel contract*;
  its letter table sits under *Type*.
- Cross-reference, 6 set queries over both indexes (66 elements). Plain text search
  put a right element first for 5 of 6 and found one in its top 5 for 6 of 6. One LLM
  call (4.3k prompt tokens, 13 s) put a right element first for 6 of 6, with no
  invalid refs, and mixed systems where both apply ("style settings for a Nature
  journal figure" → `sciplots:nature`, `kare:publication`). `design_search` (§7)
  stays plain text: the agent is already an LLM and picks from the hits.

## 13. Open questions

- Should a project pin each system's commit by default, so figures stay reproducible,
  or float on the registry ref?
- Reference panels in the exported PDF: draw the image, or an outlined blank?
- A DS without a licence (kare today): pintu reads and shows it locally. Should the UI
  flag templates derived from it?
- Should accepted recipes record which elements the agent followed (sidecar
  `design`)?
