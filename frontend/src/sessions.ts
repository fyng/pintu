import { create } from "zustand";

/** Agent sessions (docs/api-sessions.md): types, pure event reducers and the chat store. */

export type Status =
  | { type: "idle" }
  | { type: "busy" }
  | { type: "retry"; attempt: number; message: string; next: number };

export type TurnState = "running" | "open" | "empty" | "accepted" | "reverted";

export interface Turn {
  n: number;
  state: TurnState;
  outcome?: string;
  started: number;
  ended?: number;
  files: Record<string, unknown>;
  kept?: string[];
}

export interface Session {
  id: string;
  parentId: string | null;
  kind: "prompt" | "adapt" | "promote";
  title: string;
  board: string | null;
  panel: string | null;
  created: number;
  updated: number;
  status: Status;
  turns: Turn[];
  size: [number, number] | null;
  oldSize: [number, number] | null;
  profile: { name: string; model: string; tools: boolean; vision: boolean } | null;
  error: string | null;
  /** Only `stopped` when the turn was aborted. */
  result: { stopped: string; steps?: number; seconds?: number; text?: string; recipe?: string | null } | null;
  usage: { prompt?: number; completion?: number; total?: number };
  steps: number;
  transcript?: string;
}

export type ToolState =
  | { status: "pending" }
  | { status: "running"; time: { start: number } }
  | { status: "done" | "error"; time: { start: number; end: number }; output: string; error?: string; image: boolean };

export interface PatchFile {
  path: string;
  status: "added" | "modified" | "deleted";
  diff: string;
}

interface PartBase {
  id: string;
  sessionId: string;
  turn: number;
  time: { start: number; end?: number };
}

export type Part = PartBase & (
  | { type: "text"; role: "user" | "assistant"; text: string; step?: number }
  | { type: "reasoning"; text: string; step: number }
  | { type: "tool"; callId: string; tool: string; input: Record<string, unknown>; step: number; state: ToolState }
  | { type: "patch"; files: PatchFile[]; outcome: string }
);

export interface Conflict {
  path: string;
  turn: number;
  reason: string;
}

export interface LlmStatus {
  configured: boolean;
  profile?: string;
  model?: string;
  error?: string;
}

/** The part of the chat state that WebSocket events change. */
export interface Live {
  sessions: Record<string, Session>;
  /** Parts of the sessions opened in the chat panel; others are not kept. */
  parts: Record<string, Part[]>;
  /** A one-line summary of each session's latest part, for the list. */
  activity: Record<string, string>;
}

const RANK: Record<ToolState["status"], number> = { pending: 0, running: 1, done: 2, error: 2 };

/** The later of two copies of one part: tool parts only move forward. */
function later(a: Part, b: Part): Part {
  if (a.type === "tool" && b.type === "tool") return RANK[b.state.status] >= RANK[a.state.status] ? b : a;
  return b;
}

/** Merges fetched parts with those that arrived by WebSocket meanwhile, in time order, by id. */
export function mergeParts(fetched: Part[], live: Part[]): Part[] {
  const byId = new Map(fetched.map((p) => [p.id, p]));
  for (const p of live) byId.set(p.id, byId.has(p.id) ? later(byId.get(p.id)!, p) : p);
  return [...byId.values()].sort((a, b) => a.time.start - b.time.start);
}

/** A short label for the list: what a part shows the session doing. */
export function activityOf(p: Part): string {
  if (p.type === "tool") return `${p.tool} ${p.state.status}`;
  if (p.type === "patch") return `patch: ${p.files.length} file${p.files.length === 1 ? "" : "s"}`;
  if (p.type === "reasoning") return "thinking";
  return p.role === "user" ? "prompt sent" : "replied";
}

/** Applies one WebSocket message; returns the same object when it is not a session event. */
export function applyEvent(s: Live, msg: any): Live {
  switch (msg.type) {
    case "session.created":
    case "session.updated":
      return { ...s, sessions: { ...s.sessions, [msg.session.id]: msg.session } };
    case "session.status": {
      const cur = s.sessions[msg.sessionId];
      if (!cur) return s;
      return { ...s, sessions: { ...s.sessions, [msg.sessionId]: { ...cur, status: msg.status } } };
    }
    case "part.added":
    case "part.updated": {
      const part: Part = msg.part;
      const activity = { ...s.activity, [msg.sessionId]: activityOf(part) };
      const cur = s.parts[msg.sessionId];
      if (!cur) return { ...s, activity };
      const i = cur.findIndex((p) => p.id === part.id);
      const next = i < 0 ? [...cur, part] : cur.map((p, k) => (k === i ? later(p, part) : p));
      return { ...s, activity, parts: { ...s.parts, [msg.sessionId]: next } };
    }
    default:
      return s;
  }
}

/** Status text: idle, busy, or the retry attempt and seconds until the next try. */
export function statusLabel(st: Status, now: number = Date.now() / 1000): string {
  if (st.type !== "retry") return st.type;
  return `retry ${st.attempt} in ${Math.max(0, Math.ceil(st.next - now))} s`;
}

/** Turns that Accept and Revert act on: accept takes open turns ≤ n, revert open turns ≥ n. */
export function openTurns(s: Session): number[] {
  return s.turns.filter((t) => t.state === "open").map((t) => t.n);
}

/** Top-level sessions, newest first, each with its children (newest first). */
export function tree(sessions: Record<string, Session>): { session: Session; children: Session[] }[] {
  const all = Object.values(sessions).sort((a, b) => b.created - a.created);
  const ids = new Set(all.map((s) => s.id));
  return all.filter((s) => !s.parentId || !ids.has(s.parentId))
    .map((session) => ({ session, children: all.filter((c) => c.parentId === session.id) }));
}

export class ApiError extends Error {
  constructor(public status: number, public detail: any) {
    super(typeof detail === "string" ? detail : detail?.message ?? JSON.stringify(detail));
  }
}

async function call<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body === undefined ? undefined
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    const b = await r.json().catch(() => ({ detail: r.statusText }));
    throw new ApiError(r.status, b.detail);
  }
  return r.json();
}

const sid = (id: string) => `/api/sessions/${encodeURIComponent(id)}`;

export interface NewSession {
  kind: Session["kind"];
  board?: string | null;
  panel?: string | null;
  prompt?: string;
  path?: string;
  size?: [number, number];
}

export interface PromoteSource {
  path: string;
  script: string;
  recipe: string | null;
  params: Record<string, unknown>;
  size: [number, number];
  module: string;
  suggestedRecipe: string;
}

export const sessionsApi = {
  list: () => call<Session[]>("/api/sessions"),
  get: (id: string) => call<{ session: Session; parts: Part[] }>(sid(id)),
  create: (req: NewSession) => call<Session>("/api/sessions", req),
  prompt: (id: string, prompt: string) => call<Session>(`${sid(id)}/prompt`, { prompt }),
  abort: (id: string) => call<Session>(`${sid(id)}/abort`, {}),
  accept: (id: string, turn?: number) => call<{ session: Session; accepted: number[] }>(`${sid(id)}/accept`, turn === undefined ? {} : { turn }),
  revert: (id: string, turn?: number, conflicts: "fail" | "skip" | "overwrite" = "fail") =>
    call<{ session: Session; reverted: number[]; restored: string[]; conflicts: Conflict[] }>(`${sid(id)}/revert`, { turn, conflicts }),
  llm: () => call<LlmStatus>("/api/llm"),
  promoteSource: (path: string) => call<PromoteSource>(`/api/promote/source?path=${encodeURIComponent(path)}`),
};

/** A revert that hit conflicts, waiting for the user's skip or overwrite. */
export interface PendingRevert {
  sessionId: string;
  turn?: number;
  conflicts: Conflict[];
}

interface ChatState extends Live {
  /** The session open in the chat panel. */
  open: string | null;
  llm: LlmStatus | null;
  /** The last session action's error. */
  error: string | null;
  notice: string | null;
  pendingRevert: PendingRevert | null;
  /** The left sidebar's tab; starting a session switches it to the chat. */
  tab: "gallery" | "files" | "chat";
  setTab: (tab: ChatState["tab"]) => void;
  refresh: () => Promise<void>;
  show: (id: string | null) => Promise<void>;
  create: (req: NewSession) => Promise<Session | null>;
  prompt: (id: string, text: string) => Promise<void>;
  abort: (id: string) => Promise<void>;
  accept: (id: string, turn?: number) => Promise<void>;
  revert: (id: string, turn?: number, conflicts?: "fail" | "skip" | "overwrite") => Promise<void>;
  /** Notes sessions a board edit started (the `/ops` response's `adaptSessions`). */
  started: (ids: string[]) => void;
  onEvent: (msg: any) => void;
}

export const useSessions = create<ChatState>((set, get) => {
  /** Runs a session action; keeps the returned session and shows errors. */
  const act = async <T,>(fn: () => Promise<T>): Promise<T | null> => {
    try {
      const out = await fn();
      set({ error: null });
      return out;
    } catch (e) {
      set({ error: (e as Error).message });
      return null;
    }
  };
  const keep = (s: Session) => set({ sessions: { ...get().sessions, [s.id]: s } });

  return {
    sessions: {},
    parts: {},
    activity: {},
    open: null,
    llm: null,
    error: null,
    notice: null,
    pendingRevert: null,
    tab: "gallery",

    setTab: (tab) => set({ tab }),

    refresh: async () => {
      const [list, llm] = await Promise.all([sessionsApi.list().catch(() => null), sessionsApi.llm().catch(() => null)]);
      if (list) set({ sessions: Object.fromEntries(list.map((s) => [s.id, s])) });
      if (llm) set({ llm });
      const open = get().open;
      if (open) await get().show(open);
    },

    show: async (id) => {
      set({ open: id, pendingRevert: null });
      if (!id) return;
      if (!get().parts[id]) set({ parts: { ...get().parts, [id]: [] } });
      const r = await act(() => sessionsApi.get(id));
      if (!r) return;
      set({ sessions: { ...get().sessions, [id]: r.session }, parts: { ...get().parts, [id]: mergeParts(r.parts, get().parts[id] ?? []) } });
    },

    create: async (req) => {
      const s = await act(() => sessionsApi.create(req));
      if (s) {
        keep(s);
        set({ tab: "chat" });
        await get().show(s.id);
      }
      return s;
    },

    prompt: async (id, text) => {
      const s = await act(() => sessionsApi.prompt(id, text));
      if (s) keep(s);
    },

    abort: async (id) => {
      const s = await act(() => sessionsApi.abort(id));
      if (s) keep(s);
    },

    accept: async (id, turn) => {
      const r = await act(() => sessionsApi.accept(id, turn));
      if (r) keep(r.session);
    },

    revert: async (id, turn, conflicts = "fail") => {
      try {
        const r = await sessionsApi.revert(id, turn, conflicts);
        keep(r.session);
        set({ error: null, pendingRevert: null,
          notice: r.conflicts.length ? `Reverted; kept ${r.conflicts.map((c) => c.path).join(", ")} as changed` : null });
      } catch (e) {
        if (e instanceof ApiError && e.status === 409 && e.detail?.conflicts)
          return set({ pendingRevert: { sessionId: id, turn, conflicts: e.detail.conflicts }, error: null });
        set({ error: (e as Error).message });
      }
    },

    started: (ids) => {
      if (!ids.length) return;
      const names = ids.map((i) => get().sessions[i]?.panel ?? i).join(", ");
      set({ notice: `Adapt to size started for ${names}` });
    },

    onEvent: (msg) => {
      const cur = get();
      const next = applyEvent(cur, msg);
      if (next !== cur) set({ sessions: next.sessions, parts: next.parts, activity: next.activity });
    },
  };
});
