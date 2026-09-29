import { create } from "zustand";
import { api, type BoardView, type Op } from "./api";
import type { Cell } from "./geometry";
import { useSessions } from "./sessions";

interface Preview {
  svg: string;
  rev: number;
  ms: number;
}

interface State {
  boards: string[];
  board: BoardView | null;
  selected: string | null;
  /** Selected panel ids, `selected` first; shift-click adds or removes. */
  selection: string[];
  preview: Preview | null;
  error: string | null;
  warnings: string[];
  /** Milliseconds from the last commit to its preview arriving. */
  latency: number | null;
  pendingSince: number | null;
  /** Bumped when the gallery catalog changes on disk. */
  galleryRev: number;
  load: (name: string) => Promise<void>;
  select: (id: string | null) => void;
  toggleSelect: (id: string) => void;
  commit: (ops: Op[]) => Promise<void>;
  setCellLocal: (id: string, cell: Cell) => void;
  onMessage: (msg: any) => void;
}

export const useStore = create<State>((set, get) => ({
  boards: [],
  board: null,
  selected: null,
  selection: [],
  preview: null,
  error: null,
  warnings: [],
  latency: null,
  pendingSince: null,
  galleryRev: 0,

  load: async (name) => {
    const board = await api.board(name);
    set({ board, selected: null, selection: [], preview: null, error: null });
    sendWs({ type: "open", name });
  },

  select: (id) => set({ selected: id, selection: id ? [id] : [] }),

  toggleSelect: (id) => {
    const cur = get().selection;
    const selection = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    set({ selection, selected: selection[0] ?? null });
  },

  setCellLocal: (id, cell) => {
    const b = get().board;
    if (!b) return;
    set({ board: { ...b, panels: b.panels.map((p) => (p.id === id ? { ...p, cell } : p)) } });
  },

  commit: async (ops) => {
    const b = get().board;
    if (!b) return;
    set({ pendingSince: performance.now() });
    try {
      const board = await api.ops(b.name, ops);
      const cur = get().board;
      if (!cur || board.rev >= cur.rev) set({ board });
      set({ error: null, warnings: board.opWarnings ?? [] });
      useSessions.getState().started(board.adaptSessions ?? []);
    } catch (e) {
      set({ error: (e as Error).message, pendingSince: null });
      await get().load(b.name);
    }
  },

  onMessage: (msg) => {
    // Session events go to the chat store only, so they do not re-render the canvas.
    if (msg.type.startsWith("session.") || msg.type.startsWith("part.")) return useSessions.getState().onEvent(msg);
    const b = get().board;
    if (msg.type === "board" && b && msg.board.name === b.name && msg.board.rev >= b.rev) {
      set({ board: msg.board });
    } else if (msg.type === "preview" && b && msg.name === b.name) {
      if (get().preview && msg.rev < get().preview!.rev) return;
      const since = get().pendingSince;
      set({ preview: { svg: msg.svg, rev: msg.rev, ms: msg.ms }, latency: since === null ? get().latency : performance.now() - since, pendingSince: null });
    } else if (msg.type === "gallery") {
      set({ galleryRev: get().galleryRev + 1 });
    } else if (msg.type === "error" && b && msg.name === b.name) {
      set({ error: msg.message });
    }
  },
}));

let ws: WebSocket | null = null;
let queue: unknown[] = [];

function sendWs(msg: unknown) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
  else queue.push(msg);
}

/** Opens the preview socket and reconnects when it drops. */
export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  ws.onopen = () => {
    const b = useStore.getState().board;
    if (b) queue.push({ type: "open", name: b.name });
    queue.forEach((m) => ws!.send(JSON.stringify(m)));
    queue = [];
    useSessions.getState().refresh();
  };
  ws.onmessage = (e) => useStore.getState().onMessage(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1000);
}
