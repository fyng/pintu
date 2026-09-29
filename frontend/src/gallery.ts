// Gallery query helpers (SPEC §7).

export type Filters = Record<string, string[]>;

export interface GalleryItem {
  path: string;
  kind: "vector" | "raster";
  version: number;
  recipe: string | null;
  params: Record<string, unknown> | null;
  width_mm: number | null;
  height_mm: number | null;
  created: string | null;
}

export interface GalleryPage {
  total: number;
  offset: number;
  items: GalleryItem[];
  groups: { recipe: string | null; count: number }[];
}

export interface Facet {
  value: string;
  count: number;
}

/** Group selection: a recipe, "" for unlinked files, or null for all. */
export type Group = string | null;

/** Query string for `/api/gallery`; empty filter keys are dropped. */
export function galleryQuery(group: Group, q: string, filters: Filters, offset: number, limit: number): string {
  const s = new URLSearchParams();
  if (group !== null) s.set("recipe", group);
  if (q) s.set("q", q);
  const f = Object.fromEntries(Object.entries(filters).filter(([, v]) => v.length));
  if (Object.keys(f).length) s.set("filters", JSON.stringify(f));
  s.set("offset", String(offset));
  s.set("limit", String(limit));
  return s.toString();
}

/** Values typed as a comma- or space-separated list, trimmed and de-duplicated. */
export const parseValues = (text: string) => [...new Set(text.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean))];

/** Adds or removes one value of a filter key. */
export function toggleValue(filters: Filters, key: string, value: string): Filters {
  const cur = filters[key] ?? [];
  const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
  return { ...filters, [key]: next };
}

/** The value a param is filtered by, as the backend stores it: strings as is, others as JSON. */
export const paramText = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

/** A short label for an item: its params, else its file name. */
export function itemLabel(item: GalleryItem): string {
  if (item.params && Object.keys(item.params).length)
    return Object.values(item.params).map(paramText).join(" · ");
  return item.path.split("/").pop() ?? item.path;
}
