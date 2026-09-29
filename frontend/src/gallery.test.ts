import { describe, expect, it } from "vitest";
import { galleryQuery, itemLabel, paramText, parseValues, toggleValue } from "./gallery";

describe("gallery helpers", () => {
  it("builds the query string", () => {
    const s = new URLSearchParams(galleryQuery("m:f", "S0", { patient: ["S001", "S002"], arm: [] }, 60, 30));
    expect(s.get("recipe")).toBe("m:f");
    expect(s.get("q")).toBe("S0");
    expect(JSON.parse(s.get("filters")!)).toEqual({ patient: ["S001", "S002"] });
    expect([s.get("offset"), s.get("limit")]).toEqual(["60", "30"]);
    const all = new URLSearchParams(galleryQuery(null, "", {}, 0, 60));
    expect(all.has("recipe") || all.has("filters") || all.has("q")).toBe(false);
    expect(new URLSearchParams(galleryQuery("", "", {}, 0, 60)).get("recipe")).toBe("");
  });

  it("parses and toggles filter values", () => {
    expect(parseValues(" S001, S002 S001,,")).toEqual(["S001", "S002"]);
    const f = toggleValue({}, "arm", "A");
    expect(f).toEqual({ arm: ["A"] });
    expect(toggleValue(f, "arm", "A")).toEqual({ arm: [] });
  });

  it("labels items", () => {
    expect(paramText(3)).toBe("3");
    const base = { kind: "vector", version: 1, width_mm: null, height_mm: null, created: null } as const;
    expect(itemLabel({ ...base, path: "a/b.pdf", recipe: null, params: null })).toBe("b.pdf");
    expect(itemLabel({ ...base, path: "a/b.pdf", recipe: "m:f", params: { patient: "S001", n: 2 } })).toBe("S001 · 2");
  });
});
