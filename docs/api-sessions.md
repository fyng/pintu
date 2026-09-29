# Agent sessions API (B3)

The contract between the backend (`pintu/sessions.py`, `pintu/checkpoints.py`,
`pintu/server.py`, `pintu/mcp_server.py`) and the chat panel. The shape follows
opencode's server (sessions, typed parts, a status map), kept small. All paths are under
the backend's origin; bodies are JSON; times are Unix seconds (float).

## Model

A **session** is one agent conversation, optionally bound to a board panel. Each prompt
starts a **turn**. A turn starts with a checkpoint and ends with a `patch` part holding the
diff of the files the agent wrote. A turn stays `open` until it is accepted or reverted.
One top-level session may be busy per panel; any number run at once on different panels.
Child sessions (subtasks) have a `parentId` and are exempt from the one-per-panel rule;
aborting a parent aborts its children.

### Session

```jsonc
{
  "id": "ses_0199a1b2c3d4e5f6a7b8",       // time-sortable
  "parentId": null,                        // or the parent session id
  "kind": "prompt",                        // prompt | adapt | promote
  "title": "rename the y label",
  "board": "fig", "panel": "km",           // either may be null
  "created": 1790700000.123, "updated": 1790700042.5,
  "status": {"type": "idle"},              // see Status
  "turns": [Turn],
  "size": [89.0, 55.0], "oldSize": [89.0, 55.0],   // adapt: target and design size (mm); else may be null
  "promote": null,                         // promote: see promote/source, plus "fields" (prompt values)
  "profile": {"name": "glm", "model": "glm-5.3-flash", "tools": true, "vision": true},
  "error": null,                           // last turn's error text, if it failed
  "result": {"stopped": "done", "steps": 7, "seconds": 81.2, "text": "final reply",
             "recipe": "recipes.lines:lines"},   // recipe: promote only (null if none was written)
  "usage": {"prompt": 51234, "completion": 2345, "total": 53579},   // summed over turns
  "steps": 7,                              // model calls, summed over turns
  "transcript": ".pintu/agent/20260929-141500-123456-km.jsonl",
  "extra": []                              // internal
}
```

`result.stopped`: `done` (the model stopped calling tools), `step_cap` (20 model calls per
turn), `aborted`, `error`.

### Turn

```jsonc
{
  "n": 1,
  "state": "open",          // running | open | empty | accepted | reverted
  "outcome": "done",        // as result.stopped, or "interrupted" (server stopped mid-turn)
  "started": 1790700000.2, "ended": 1790700042.4,
  "checkpoint": {"kind": "git", "ref": "refs/pintu/checkpoints/ses_…/1", "commit": "…", "files": "…"}
             // or {"kind": "copy", "dir": ".pintu/checkpoints/ses_…/1"}
  "files": {"recipes/km.py": {"before": "<blob sha or copy path>", "beforeHash": "<sha256>",
                               "after": "…", "afterHash": "<sha256>"}},   // null before = the file did not exist
  "kept": ["recipes/km.py"]   // only after a revert with conflicts=skip: files left as the user changed them
}
```

`empty` means the turn wrote no file (its checkpoint is dropped at once).

### Status

`{"type": "idle"}`, `{"type": "busy"}`, or
`{"type": "retry", "attempt": 1, "message": "InternalServerError: …", "next": 1790700005.0}`
while waiting to retry a transient model error (connection, timeout, 408/409/429, 5xx; up to
3 retries, 2, 5 and 10 s apart).

### Parts

Every part has `id` (`prt_…`), `sessionId`, `turn` (the turn number), `type`, and
`time: {start, end?}`. Parts are appended in order; only `tool` parts change after they
are added.

| `type` | Fields |
|---|---|
| `text` | `role`: `user` (the prompt as sent, templated for adapt and promote) or `assistant`; `text`; `step` (assistant) |
| `reasoning` | `text`, `step` |
| `tool` | `callId`, `tool` (name), `input` (arguments), `step`, `state` (below) |
| `patch` | `files: [{path, status, diff}]` (`status`: `added`, `modified`, `deleted`; `diff`: unified diff), `outcome` |

Tool `state`:

```jsonc
{"status": "pending"}
{"status": "running", "time": {"start": …}}
{"status": "done",  "time": {"start": …, "end": …}, "output": "…", "image": false}
{"status": "error", "time": {…}, "output": "error: …", "error": "…", "image": false}
```

`output` is capped at 20 000 characters. `image: true` means a render image went to the
model (it is not stored). A turn that is aborted or interrupted leaves its unfinished
tool parts in `error` with `error: "aborted"` or `"interrupted: the server stopped"`.

## REST

Errors are FastAPI's `{"detail": …}` with 400 (bad request), 404 (unknown session, board,
panel or file) or 409 (busy, or a revert conflict).

| Method and path | Body / query | Returns |
|---|---|---|
| `GET /api/sessions` | `?board=&panel=` (optional filters) | `[Session]`, newest first |
| `GET /api/sessions/status` | – | `{sessionId: Status}` for sessions that are not idle |
| `POST /api/sessions` | `NewSession` | `Session` (busy if a first turn started) |
| `GET /api/sessions/{id}` | – | `{"session": Session, "parts": [Part]}` |
| `GET /api/sessions/{id}/diff` | `?turn=n` (default: all open turns) | `{"sessionId", "turn", "files": [{path, status, diff}], "diff": "<all files>"}` |
| `POST /api/sessions/{id}/prompt` | `{"prompt": "…", "size": [w, h]?}` | `Session`; 409 if busy, 400 if empty |
| `POST /api/sessions/{id}/abort` | – | `Session`, after the turn has stopped |
| `POST /api/sessions/{id}/accept` | `{"turn": n}?` (default: all open turns; accepts turns ≤ n) | `{"session", "accepted": [n…]}` |
| `POST /api/sessions/{id}/revert` | `{"turn": n?, "conflicts": "fail"}` (reverts open turns ≥ n; default all) | `{"session", "reverted": [n…], "restored": [path…], "conflicts": [Conflict]}` |
| `GET /api/promote/source` | `?path=<gallery item>` | `{path, script, recipe, params, size, module, suggestedRecipe}`; 400 if the script is not known |
| `GET /api/llm` | – | `{"configured": true, "profile", "model", "tools", "vision"}`, or `{"configured": false, "error": "no LLM config at …"}` |

`NewSession`:

```jsonc
{
  "kind": "prompt",        // prompt | adapt | promote
  "board": "fig",          // prompt: optional; adapt: required
  "panel": "km",           // prompt: optional (binds the session; any panel); adapt: a recipe panel
  "prompt": "…",           // prompt: the first prompt (without one, no turn starts);
                           // adapt/promote: optional extra instructions appended to the template
  "size": [178, 55],       // adapt: target size in mm (default: the panel's size below its letter band)
  "oldSize": [89, 55],     // adapt: size the recipe was designed for (default: its last render)
  "path": "plots/lines.pdf", // promote: the gallery item
  "parentId": null,        // make a child session; board defaults to the parent's
  "title": null
}
```

Without an LLM profile, `POST /api/sessions` with a prompt (and any adapt or promote) is a
400 (`cannot start the agent: …`) and no session is created; `GET /api/llm` tells the chat
panel so beforehand.

A prompt about a panel whose source is a static file gets the file's fit into the plot area,
the script that drew it, and the tools `write_file`, `render_recipe` and `use_recipe`, so the
agent can turn the file into a recipe in the same cell (SPEC §9). No session gets `set_cell`.
The 409 for a busy panel has
`detail: {"message": "busy", "sessionId": "<the busy one>"}`.

**Accept** drops the checkpoints and keeps the files. **Revert** restores, newest turn
first, only the files the agent wrote, to their content just before the agent's first
write in each turn (a file the agent created is deleted). A file whose content on disk is
no longer what the agent left is a `Conflict`:
`{"path": "recipes/km.py", "turn": 2, "reason": "changed after the agent's edit"}` (or
`"deleted after the agent's edit"`). `conflicts`:

- `fail` (default): nothing changes; 409 with `detail: {"message": "conflict", "conflicts": [Conflict]}`;
- `skip`: restore the others, keep the conflicting files (listed in `conflicts` and the turn's `kept`);
- `overwrite`: restore all.

Staged revert is `revert` with `turn`: it undoes turns n, n+1, … and leaves earlier ones
open. Accept and revert are refused (409) while the session is busy. The board file counts
as a file: a revert of an agent's `use_recipe` conflicts if the user edited the board since.

### Board changes

`POST /api/boards/{name}/ops` returns, besides the board and `opWarnings`,
`adaptSessions: [sessionId…]`: the "Adapt to size" sessions it started. One starts for
each recipe panel whose size (below its letter band) changed and now lies outside the
recipe's `@panel(min_size, max_size)`, unless the panel has a busy session. If no LLM
profile is set up, none starts (logged). MCP `set_cell` (when served) does the same.

Board view (`GET /api/boards/{name}` and the `board` WebSocket message): each recipe panel
has `sizeRange: {"min": [w, h] | null, "max": [w, h] | null, "outside": bool}`, or `null`
when the recipe declares no range. `outside` drives the size badge and its "Adapt to size"
button (`POST /api/sessions {"kind": "adapt", "board", "panel"}`). Each static-file panel
has `fit: {"natural": [w, h], "drawn": [w, h], "area": [w, h], "fill": [fx, fy], "problem":
str | null}` (sizes in mm; `fill` is drawn over area per axis), or `null` if the file does
not load; `problem` is set when `fill` is below 0.95 on either axis.

## WebSocket

On the existing `/api/ws`, broadcast to every client, in order:

| `type` | Fields |
|---|---|
| `session.created` | `session` |
| `session.updated` | `session` (after each model step, accept, revert and turn end) |
| `session.status` | `sessionId`, `status` |
| `part.added` | `sessionId`, `part` |
| `part.updated` | `sessionId`, `part` (tool parts: running, done, error) |

A turn emits `session.status` busy, `part.added` (the user text), then per step
`part.added` for reasoning, assistant text and each tool call (pending), `part.updated`
(running, then done or error), `session.updated`; at the end `part.added` (patch, if any
file changed), `session.status` idle and `session.updated`. Files the agent wrote reach the
board as usual: `board` and `preview` messages after a recipe re-render or a board reload.

## Persistence

`.pintu/sessions/<id>.json` holds `{session, parts, messages}` (`messages`: the model
history, images replaced by text), rewritten on each change. On start the backend loads
them all; a session that was busy comes back idle with its running turn closed as
`interrupted`. A later prompt continues the same model history.

## Checkpoints

- **git** (the project is inside a work tree): at the start of a turn, the tracked working
  tree is committed with a temporary index (a copy of the user's, then `git add -u`) and
  `git commit-tree` onto `refs/pintu/checkpoints/<session>/<turn>`; the user's index,
  HEAD and branches are untouched. Before its first write to a file in a turn, the agent's
  guard stores the file's content as a blob (`git hash-object -w`), and after each write
  the new content; at the end of the turn a second commit on the same ref holds them
  (`before/<path>`, `after/<path>`), so gc keeps them. Untracked files are covered by
  these blobs, not by the snapshot.
- **copy** (no git): the same contents as files in `.pintu/checkpoints/<session>/<turn>/before|after/<path>`.

Accept or revert deletes the ref or folder.

## Entry points

| Kind | Prompt |
|---|---|
| `prompt` | The user's text, and the panel's cell, letter band and plot area. With a recipe panel: the recipe source, sizes, lint and render (as `pintu agent`). With a static-file panel: the file's fit, the file as placed, its script and `agent.STATIC`, the steps to a recipe in the same cell. |
| `adapt` | `agent.ADAPT` with the old and new size; plus the `@panel` range and, if the new size lies outside it, a request to widen it. |
| `promote` | `agent.PROMOTE`: write a new module `recipes/<stem>.py` with `def <stem>(w, h, **params)` decorated with `@pintu_sdk.panel(min_size, max_size)`, drawing the plot of the given gallery item; tools add `write_file` and `render_recipe`; the reply ends with `RECIPE: module:function`, which becomes `result.recipe` if it exists. |

**Promote: the source script** of a gallery item is its sidecar's `script` field (a
root-relative path); `pintu_sdk.save(fig, path, script=None)` fills it with the running
`__main__` file when that is under the project root. Without it, the module file of the
sidecar's `recipe` is used, else the first project `.py` file (outside hidden folders and
`pintu_out/`) whose text names the file. Otherwise the item cannot be promoted (400). The chat panel can
call `GET /api/promote/source?path=` to show or hide "Promote to recipe". To place the
result, add a panel with `{"op": "add", "cell": […], "recipe": result.recipe}`.

## MCP server

Tools on the board, for terminal agents (Claude Code, Codex, opencode):

| Tool | Arguments | Returns |
|---|---|---|
| `get_board` | `board` | `page` (size, grid, gutter, `pitch_mm`, `letter_band_mm`), `geometry` (how cells map to mm), `layout`; panels with `letter`, `cell`, `cell_mm`, `band_mm`, `plot_mm`, `source` and, for a static file, `fit` (`natural_mm`, `drawn_mm`, `fill`) |
| `set_cell` | `board`, `panel`, `cell: [x0, y0, x1, y1]` | `{panel, warnings, adaptSessions}`; served only with `pintu serve --mcp-allow-layout` or `pintu mcp --allow-layout` |
| `render_panel` | `board`, `panel`, `w?`, `h?`, `image?` | render report (status, lint, layout summary), plus a PNG if `image`; for a static file, its fit, and the file as placed if `image` |
| `lint` | `board`, `panel?` | `{board, panels: {id: [{rule, message}]}}`; a static file under-filling its plot area gives rule `fit` |

Resources: `pintu://boards` (board names), `pintu://boards/{name}` (as `get_board`).

Transports:

- **Streamable HTTP** at `http://127.0.0.1:8765/mcp` on a running `pintu serve`: acts on
  the open board (edits reach the browser at once; renders share its kernel; resizes may
  start Adapt to size sessions). `claude mcp add --transport http pintu http://127.0.0.1:8765/mcp`.
- **stdio**: `pintu mcp --project .` runs the same tools on a headless board state of
  its own (its own kernel). Board edits go to the board file, which a running
  `pintu serve` reloads through its file watch. It starts no agent sessions.
