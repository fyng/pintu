import { describe, expect, it } from "vitest";
import { append, move, place, remove, reshape, shape, span, spans } from "./multiples";

describe("mosaic edits", () => {
  it("reads spans in reading order", () => {
    const s = spans([["A", "A", "B"], ["C", ".", "B"]]);
    expect([...s.keys()]).toEqual(["A", "B", "C"]);
    expect(s.get("A")).toEqual({ r0: 0, c0: 0, r1: 1, c1: 2 });
    expect(s.get("B")).toEqual({ r0: 0, c0: 2, r1: 2, c1: 3 });
  });

  it("reshapes by padding, cropping or re-laying out", () => {
    expect(reshape([["A", "B", "C"]], 1, 4)).toEqual([["A", "B", "C", "."]]);
    expect(reshape([["A", "B"], [".", "."]], 1, 2)).toEqual([["A", "B"]]);
    expect(reshape([["A", "B", "C", "D", "E"]], 2, 3)).toEqual([["A", "B", "C"], ["D", "E", "."]]);
    expect(shape(reshape([["A"]], 3, 2))).toEqual([3, 2]);
  });

  it("swaps items to reorder them", () => {
    expect(move([["A", "B", "C"]], "C", 0, 0)).toEqual([["C", "B", "A"]]);
    // Different sizes: one cell each at the other's top-left.
    expect(move([["A", "A", "B"]], "B", 0, 0)).toEqual([["B", ".", "A"]]);
    // Same size spans swap whole.
    expect(move([["A", "B"], ["A", "B"]], "A", 1, 1)).toEqual([["B", "A"], ["B", "A"]]);
  });

  it("moves into empty cells, keeping the span where it fits", () => {
    expect(move([["A", "A", "."], [".", ".", "."]], "A", 1, 1)).toEqual([[".", ".", "."], [".", "A", "A"]]);
    expect(move([["A", "A", "."]], "A", 0, 2)).toEqual([[".", ".", "A"]]);
    expect(move([["A", "."]], "A", 0, 0)).toEqual([["A", "."]]);
  });

  it("spans and shrinks, never over another item", () => {
    expect(span([["A", ".", "."], [".", ".", "B"]], "A", 1, 1)).toEqual([["A", "A", "."], ["A", "A", "B"]]);
    expect(span([["A", "A"], ["A", "A"]], "A", 0, 0)).toEqual([["A", "."], [".", "."]]);
    const blocked = [["A", "B"]];
    expect(span(blocked, "A", 0, 1)).toBe(blocked);
  });

  it("places gallery drops, removes and appends", () => {
    expect(place([["A", "."]], "B", 0, 1)).toEqual([["A", "B"]]);
    expect(place([["A", "A"]], "B", 0, 1)).toEqual([[".", "B"]]);
    expect(place([["A", "."]], "A", 0, 1)).toEqual([[".", "A"]]);
    expect(remove([["A", "A", "B"]], "A")).toEqual([[".", ".", "B"]]);
    expect(append([["A", "."]], "B")).toEqual([["A", "B"]]);
    expect(append([["A"], ["B"]], "C")).toEqual([["A", "C"], ["B", "."]]);
    expect(append([["A"]], "A")).toEqual([["A"]]);
  });
});
