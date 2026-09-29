# Feature wishlist

Features planned but not scheduled. Each entry gives the need, a sketch and open questions.

## Figure-level layout optimization

**Need.** Some requests are about the whole figure, not one plot. Examples: "make room
for a sixth panel", "balance the two rows", "fit these panels into a full-width
figure". Today the agent cannot change the layout: the user owns every cell (SPEC §9,
Ownership), and no session is offered `set_cell`.

**Sketch.** A separate session kind, `layout`, started only by an explicit action, not
by a free prompt. Only this kind gets `set_cell` (`agent.LAYOUT_TOOLS`), under these
guardrails:

- **Scope.** The user picks which panels may move or resize. The others are pinned,
  and `set_cell` refuses them.
- **Proposals, not edits.** The agent works on a draft copy of the board. Nothing
  reaches the board file until the user accepts the proposal as a whole, after
  previewing it on the canvas.
- **Hard constraints**, checked by pintu on each proposal:
  - no overlaps, and cells on the grid;
  - page width among the style's widths, and height within its cap;
  - each recipe panel's plot area within its `@panel` size range, or an Adapt to
    size session is queued for it;
  - no static file's fit worse than before;
  - reading order and letters unchanged, unless the user allows it.
- **No probing by writes.** A read-only `check_layout(cells)` tool returns each
  panel's size, fit and any constraint it breaks. The agent never moves a panel to
  measure the grid.
- **A score in every result**: empty plot area, aligned edges, and each plot's
  aspect against its natural aspect. A cap on proposals per turn.
- **Undo.** An accepted proposal is one checkpointed turn, and revert restores the
  layout.

**Open questions.**

- What should the score weigh: fill, edge alignment, or closeness to each plot's
  natural aspect?
- May the agent change the page height, or only the cells?
- How do Adapt to size sessions follow an accepted proposal: one per resized panel, or
  one child session per proposal?
