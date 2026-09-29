// Page grid geometry, mirroring backend/src/pintu/geometry.py (SPEC §6).

export type Cell = [number, number, number, number];
export type Handle = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export interface Page {
  width: number;
  height: number;
  grid: [number, number];
  gutter: number;
}

export const pitch = (length: number, n: number, gutter: number) => (length + gutter) / n;

/** Start and length in mm of the span between grid lines a and b. */
export function span(length: number, n: number, a: number, b: number, gutter: number): [number, number] {
  const p = pitch(length, n, gutter);
  return [a * p, (b - a) * p - gutter];
}

/** The cell's [x, y, w, h] in mm. */
export function rect(page: Page, c: Cell): [number, number, number, number] {
  const [x, w] = span(page.width, page.grid[0], c[0], c[2], page.gutter);
  const [y, h] = span(page.height, page.grid[1], c[1], c[3], page.gutter);
  return [x, y, w, h];
}

/** Grid line position in mm. */
export const lineAt = (length: number, n: number, gutter: number, i: number) => i * pitch(length, n, gutter);

/** Nearest grid line to a position in mm, clamped to [0, n]. */
export function snap(mm: number, length: number, n: number, gutter: number): number {
  return Math.max(0, Math.min(n, Math.round(mm / pitch(length, n, gutter))));
}

export const overlaps = (a: Cell, b: Cell) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

export const sameCell = (a: Cell, b: Cell) => a.every((v, i) => v === b[i]);

/** The cell after dragging a handle from `start` (mm) to `now` (mm). */
export function dragCell(page: Page, cell: Cell, handle: Handle, start: [number, number], now: [number, number]): Cell {
  const [nx, ny] = page.grid;
  const [px, py] = [pitch(page.width, nx, page.gutter), pitch(page.height, ny, page.gutter)];
  let [x0, y0, x1, y1] = cell;
  if (handle === "move") {
    const dx = Math.max(-x0, Math.min(nx - x1, Math.round((now[0] - start[0]) / px)));
    const dy = Math.max(-y0, Math.min(ny - y1, Math.round((now[1] - start[1]) / py)));
    return [x0 + dx, y0 + dy, x1 + dx, y1 + dy];
  }
  // Edges snap to the nearest line; x1/y1 lines sit a gutter past the panel's far edge.
  const lx = (mm: number, far: boolean) => snap(far ? mm + page.gutter : mm, page.width, nx, page.gutter);
  const ly = (mm: number, far: boolean) => snap(far ? mm + page.gutter : mm, page.height, ny, page.gutter);
  const [ox, oy] = [now[0] - start[0], now[1] - start[1]];
  const r = rect(page, cell);
  if (handle.includes("w")) x0 = Math.min(lx(r[0] + ox, false), x1 - 1);
  if (handle.includes("e")) x1 = Math.max(lx(r[0] + r[2] + ox, true), x0 + 1);
  if (handle.includes("n")) y0 = Math.min(ly(r[1] + oy, false), y1 - 1);
  if (handle.includes("s")) y1 = Math.max(ly(r[1] + r[3] + oy, true), y0 + 1);
  return [x0, y0, x1, y1];
}

/** Grid lines that are exact halves to sixths of an axis with n units. */
export function coarseLines(n: number): number[] {
  const s = new Set<number>();
  for (const d of [2, 3, 4, 5, 6]) if (n % d === 0) for (let i = 1; i < d; i++) s.add((i * n) / d);
  return [...s].sort((a, b) => a - b);
}

/**
 * The cell a file dropped at grid unit (ux, uy) fills: the unit grows through
 * empty units, within the page third that holds it. Null if the unit is taken.
 */
export function dropCell(page: Page, cells: Cell[], ux: number, uy: number): Cell | null {
  const [nx, ny] = page.grid;
  const free = (c: Cell) => c[0] >= 0 && c[1] >= 0 && c[2] <= nx && c[3] <= ny && !cells.some((o) => overlaps(o, c));
  let c: Cell = [ux, uy, ux + 1, uy + 1];
  if (!free(c)) return null;
  const bx = Math.max(1, Math.floor(nx / 3));
  const by = Math.max(1, Math.floor(ny / 3));
  const lim: Cell = [
    Math.floor(ux / bx) * bx, Math.floor(uy / by) * by,
    Math.min(nx, (Math.floor(ux / bx) + 1) * bx), Math.min(ny, (Math.floor(uy / by) + 1) * by),
  ];
  for (let grown = true; grown; ) {
    grown = false;
    const tries: Cell[] = [
      [c[0], c[1], c[2] + 1, c[3]], [c[0], c[1], c[2], c[3] + 1],
      [c[0] - 1, c[1], c[2], c[3]], [c[0], c[1] - 1, c[2], c[3]],
    ];
    for (const t of tries) {
      if (t[0] >= lim[0] && t[1] >= lim[1] && t[2] <= lim[2] && t[3] <= lim[3] && free(t)) {
        c = t;
        grown = true;
      }
    }
  }
  return c;
}

/** Whether splitting `units` into n parts is exact. */
export const splitExact = (units: number, n: number) => units % n === 0;
