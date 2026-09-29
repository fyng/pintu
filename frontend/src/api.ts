import type { Cell, Page } from "./geometry";

export interface Panel {
  id: string;
  cell: Cell;
  rect: [number, number, number, number];
  letter: string | null;
  letterSetting: string | null;
  source: { file?: string; recipe?: string };
  file: string | null;
  fileVersion: number | null;
  kind: "vector" | "raster" | null;
  /** Recipe panels only. */
  params?: Record<string, unknown>;
  render?: RenderStatus;
}

export interface RenderStatus {
  status: "rendering" | "ok" | "error" | "missing";
  seconds?: number;
  cached?: boolean;
  error?: string;
  stdout?: string;
  stderr?: string;
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
  preset: { widths: Record<string, number>; maxHeight: number; letterZone: number };
  panels: Panel[];
  warnings: string[];
  opWarnings?: string[];
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
  pdfUrl: (name: string) => `/api/boards/${encodeURIComponent(name)}/preview.pdf`,
};
