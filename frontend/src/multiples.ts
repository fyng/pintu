// Inner-grid edits of a multiples panel's mosaic (SPEC §8). Every edit returns a
// new mosaic in `Figure.subplot_mosaic` form: a repeated value spans a
// rectangle of cells, "." is an empty cell.

export type Mosaic = string[][];
export const EMPTY = ".";

/** Rows [r0, r1) and columns [c0, c1) an item covers. */
export interface Span {
  r0: number;
  c0: number;
  r1: number;
  c1: number;
}

export const shape = (m: Mosaic): [number, number] => [m.length, m[0]?.length ?? 0];

/** Each item's span, in reading order of its first cell. */
export function spans(m: Mosaic): Map<string, Span> {
  const out = new Map<string, Span>();
  m.forEach((row, r) => row.forEach((v, c) => {
    if (v === EMPTY) return;
    const s = out.get(v);
    if (!s) out.set(v, { r0: r, c0: c, r1: r + 1, c1: c + 1 });
    else Object.assign(s, { r0: Math.min(s.r0, r), c0: Math.min(s.c0, c), r1: Math.max(s.r1, r + 1), c1: Math.max(s.c1, c + 1) });
  }));
  return out;
}

const copy = (m: Mosaic): Mosaic => m.map((row) => [...row]);
const fill = (m: Mosaic, s: Span, v: string) => {
  for (let r = s.r0; r < s.r1; r++) for (let c = s.c0; c < s.c1; c++) m[r][c] = v;
};
const clear = (m: Mosaic, v: string) => m.forEach((row) => row.forEach((x, c) => { if (x === v) row[c] = EMPTY; }));
/** Whether every cell of a span lies in the grid and holds `v` or is empty. */
const freeFor = (m: Mosaic, s: Span, v: string) => {
  const [rows, cols] = shape(m);
  if (s.r0 < 0 || s.c0 < 0 || s.r1 > rows || s.c1 > cols) return false;
  for (let r = s.r0; r < s.r1; r++) for (let c = s.c0; c < s.c1; c++) if (m[r][c] !== EMPTY && m[r][c] !== v) return false;
  return true;
};

/**
 * The mosaic at rows × cols. Items keep their cells when they all fit; otherwise
 * they are laid out one cell each in reading order, and any past the last cell drop.
 */
export function reshape(m: Mosaic, rows: number, cols: number): Mosaic {
  const out: Mosaic = Array.from({ length: rows }, () => Array(cols).fill(EMPTY));
  const items = [...spans(m)];
  if (items.every(([, s]) => s.r1 <= rows && s.c1 <= cols)) {
    for (const [v, s] of items) fill(out, s, v);
    return out;
  }
  items.slice(0, rows * cols).forEach(([v], i) => { out[Math.floor(i / cols)][i % cols] = v; });
  return out;
}

/**
 * Moves an item to cell (r, c). Onto another item: the two swap (whole spans if
 * the same size, else one cell each at the other's top-left). Onto an empty
 * cell: the item keeps its span with its top-left there if that fits, else takes
 * the one cell.
 */
export function move(m: Mosaic, v: string, r: number, c: number): Mosaic {
  const all = spans(m);
  const s = all.get(v);
  const target = m[r]?.[c];
  if (!s || target === undefined || target === v) return m;
  const out = copy(m);
  const h = s.r1 - s.r0, w = s.c1 - s.c0;
  if (target !== EMPTY) {
    const t = all.get(target)!;
    clear(out, v);
    clear(out, target);
    if (t.r1 - t.r0 === h && t.c1 - t.c0 === w) {
      fill(out, t, v);
      fill(out, s, target);
    } else {
      out[t.r0][t.c0] = v;
      out[s.r0][s.c0] = target;
    }
    return out;
  }
  const at = { r0: r, c0: c, r1: r + h, c1: c + w };
  clear(out, v);
  if (freeFor(out, at, v)) fill(out, at, v);
  else out[r][c] = v;
  return out;
}

/**
 * Stretches or shrinks an item to span from its top-left cell to cell (r, c)
 * (a target above or left of it moves that corner). Unchanged if the new span
 * would cover another item.
 */
export function span(m: Mosaic, v: string, r: number, c: number): Mosaic {
  const s = spans(m).get(v);
  if (!s || m[r]?.[c] === undefined) return m;
  const to = { r0: Math.min(s.r0, r), c0: Math.min(s.c0, c), r1: Math.max(s.r0, r) + 1, c1: Math.max(s.c0, c) + 1 };
  if (!freeFor(m, to, v)) return m;
  const out = copy(m);
  clear(out, v);
  fill(out, to, v);
  return out;
}

/** Puts a new item (e.g. a gallery drop) in cell (r, c), replacing the item there; an item already present moves. */
export function place(m: Mosaic, v: string, r: number, c: number): Mosaic {
  if (m[r]?.[c] === undefined) return m;
  if (spans(m).has(v)) return move(m, v, r, c);
  const out = copy(m);
  if (out[r][c] !== EMPTY) clear(out, out[r][c]);
  out[r][c] = v;
  return out;
}

/** Empties an item's cells. */
export function remove(m: Mosaic, v: string): Mosaic {
  const out = copy(m);
  clear(out, v);
  return out;
}

/** Adds an item in the first empty cell, or in a new column at the top; unchanged if present. */
export function append(m: Mosaic, v: string): Mosaic {
  if (spans(m).has(v)) return m;
  for (let r = 0; r < m.length; r++) for (let c = 0; c < m[r].length; c++) if (m[r][c] === EMPTY) return place(m, v, r, c);
  return m.map((row, r) => [...row, r === 0 ? v : EMPTY]);
}
