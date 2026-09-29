import type { Facet, GalleryPage } from "./gallery";
import type { Cell, Page } from "./geometry";

export interface Panel {
  id: string;
  cell: Cell;
  rect: [number, number, number, number];
  band: number;
  letter: string | null;
  letterSetting: string | null;
  source: { file?: string; recipe?: string };
  file: string | null;
  fileVersion: number | null;
  kind: "vector" | "raster" | null;
  /** How a static file fills the plot area (static-file panels only; null if the file does not load). */
  fit?: Fit | null;
  /** The group the panel belongs to, if any. */
  group: string | null;
  /** Recipe panels only. */
  params?: Record<string, unknown>;
  render?: RenderStatus;
  /** Item param of a `@multiples` recipe, else null (recipe panels only). */
  multiplesItem?: string | null;
  multiples?: Multiples;
  /** The recipe's `@panel` size range (recipe panels only; null without one). */
  sizeRange?: SizeRange | null;
}

/** A static file's contain fit into its plot area, sizes in mm. */
export interface Fit {
  natural: [number, number];
  drawn: [number, number];
  area: [number, number];
  /** Drawn size over the plot area, per axis, 0 to 1. */
  fill: [number, number];
  /** How much of the plot area stays empty, when that is a problem. */
  problem: string | null;
}

export interface SizeRange {
  min: [number, number] | null;
  max: [number, number] | null;
  outside: boolean;
}

export type ShareMode = "all" | "row" | "col" | "none";

/** A multiples panel's `source.multiples`, with its cell size and any reflow on offer. */
export interface Multiples {
  item: string;
  mosaic: string[][];
  share: { x: ShareMode; y: ShareMode };
  width_ratios?: number[];
  height_ratios?: number[];
  minCell: [number, number] | null;
  cellMm: [number, number];
  reflow: string[][] | null;
}

export interface Group {
  id: string;
  panels: string[];
  cell: Cell;
  rect: [number, number, number, number];
  letter: string | null;
  letterSetting: string | null;
}

export interface RenderStatus {
  status: "rendering" | "ok" | "error" | "missing";
  seconds?: number;
  cached?: boolean;
  error?: string;
  stdout?: string;
  stderr?: string;
  /** Lint issues against the style pack (SPEC §8). */
  lint?: LintIssue[];
}

export interface LintIssue {
  rule: string;
  message: string;
}

export interface RecipeLocation {
  recipe: string;
  file: string;
  line: number;
  text: string;
}

export interface BoardView {
  name: string;
  rev: number;
  page: Page & { style: string };
  preset: { pack: string; name: string; widths: Record<string, number>; maxHeight: number; letterBand: number };
  panels: Panel[];
  groups: Group[];
  warnings: string[];
  opWarnings?: string[];
  /** Adapt to size sessions a board edit started. */
  adaptSessions?: string[];
}

export interface Entry {
  name: string;
  path: string;
  dir: boolean;
  kind: "vector" | "raster" | null;
  size: number | null;
}

export type Op = Record<string, unknown> & { op: string };

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  }
  return r.json();
}

const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  project: () => fetch("/api/project").then((r) => json<{ name: string; boards: string[] }>(r)),
  board: (name: string) => fetch(`/api/boards/${encodeURIComponent(name)}`).then((r) => json<BoardView>(r)),
  newBoard: (name: string) => post("/api/boards", { name }).then((r) => json<BoardView>(r)),
  ops: (name: string, ops: Op[]) => post(`/api/boards/${encodeURIComponent(name)}/ops`, { ops }).then((r) => json<BoardView>(r)),
  files: (path: string) => fetch(`/api/files?path=${encodeURIComponent(path)}`).then((r) => json<{ entries: Entry[] }>(r)),
  thumbUrl: (path: string, version?: number | null) =>
    `/api/thumb?path=${encodeURIComponent(path)}${version ? `&v=${version}` : ""}`,
  locate: (recipe: string) => fetch(`/api/recipes/locate?recipe=${encodeURIComponent(recipe)}`).then((r) => json<RecipeLocation>(r)),
  openRecipe: (recipe: string) => post("/api/recipes/open", { recipe }).then((r) => json<{ opened: boolean; command: string | null }>(r)),
  gallery: (query: string) => fetch(`/api/gallery?${query}`).then((r) => json<GalleryPage>(r)),
  facets: (recipe: string) =>
    fetch(`/api/gallery/facets?recipe=${encodeURIComponent(recipe)}`).then((r) => json<{ params: Record<string, Facet[]> }>(r)),
  galleryThumbUrl: (path: string, version: number) => `/api/gallery/thumb?path=${encodeURIComponent(path)}&v=${version}`,
  pdfUrl: (name: string) => `/api/boards/${encodeURIComponent(name)}/preview.pdf`,
};
