import { describe, expect, it } from "vitest";
import { coarseLines, dragCell, dropCell, pitch, rect, snap, type Cell, type Page } from "./geometry";

const page: Page = { width: 183, height: 170, grid: [36, 36], gutter: 3 };
const p = pitch(183, 36, 3);

describe("snap", () => {
  it("rounds to the nearest line and clamps", () => {
    expect(snap(0.49 * p, 183, 36, 3)).toBe(0);
    expect(snap(0.51 * p, 183, 36, 3)).toBe(1);
    expect(snap(17.6 * p, 183, 36, 3)).toBe(18);
    expect(snap(-10, 183, 36, 3)).toBe(0);
    expect(snap(999, 183, 36, 3)).toBe(36);
  });

  it("matches fig-span for halves", () => {
    const [x, , w] = rect(page, [18, 0, 36, 6]);
    expect(x).toBeCloseTo((183 - 3) / 2 + 3, 9);
    expect(w).toBeCloseTo((183 - 3) / 2, 9);
  });
});

describe("dragCell", () => {
  const c: Cell = [6, 6, 12, 12];
  it("moves by whole units and stays on the page", () => {
    expect(dragCell(page, c, "move", [0, 0], [2.4 * p, -0.4 * p])).toEqual([8, 6, 14, 12]);
    expect(dragCell(page, c, "move", [0, 0], [-100 * p, 0])).toEqual([0, 6, 6, 12]);
    expect(dragCell(page, c, "move", [0, 0], [100 * p, 100 * p])).toEqual([30, 30, 36, 36]);
  });
  it("resizes edges and corners to the nearest line", () => {
    expect(dragCell(page, c, "e", [0, 0], [3.3 * p, 0])).toEqual([6, 6, 15, 12]);
    expect(dragCell(page, c, "w", [0, 0], [-1.6 * p, 0])).toEqual([4, 6, 12, 12]);
    expect(dragCell(page, c, "se", [0, 0], [p, 2 * p])).toEqual([6, 6, 13, 14]);
    expect(dragCell(page, c, "nw", [0, 0], [0.2 * p, -0.7 * p])).toEqual([6, 5, 12, 12]);
  });
  it("keeps at least one unit", () => {
    expect(dragCell(page, c, "e", [0, 0], [-50 * p, 0])).toEqual([6, 6, 7, 12]);
    expect(dragCell(page, c, "n", [0, 0], [0, 50 * p])).toEqual([6, 11, 12, 12]);
  });
});

describe("guides and drops", () => {
  it("coarse lines are halves to sixths", () => {
    expect(coarseLines(36)).toEqual([6, 9, 12, 18, 24, 27, 30]);
    expect(coarseLines(30)).toEqual([5, 6, 10, 12, 15, 18, 20, 24, 25]);
  });
  it("a drop fills the empty part of its third", () => {
    expect(dropCell(page, [], 3, 3)).toEqual([0, 0, 12, 12]);
    expect(dropCell(page, [[0, 0, 12, 4]], 3, 6)).toEqual([0, 4, 12, 12]);
    expect(dropCell(page, [[0, 0, 12, 4]], 3, 2)).toBeNull();
  });
});
