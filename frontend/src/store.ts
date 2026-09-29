import { create } from "zustand";
import { api, type BoardView, type Op } from "./api";
import type { Cell } from "./geometry";

interface Preview {
  svg: string;
  rev: number;
  ms: number;
}

interface State {
  boards: string[];
  board: BoardView | null;
  selected: string | null;
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
  commit: (ops: Op[]) => Promise<void>;
  setCellLocal: (id: string, cell: Cell) => void;
  onMessage: (msg: any) => void;
}

export const useStore = create<State>((set, get) => ({
  boards: [],
  board: null,
  selected: null,
  preview: null,
  error: null,
  warnings: [],
  latency: null,
  pendingSince: null,
  galleryRev: 0,

  load: async (name) => {
    const board = await api.board(name);
    set({ board, selected: null, preview: null, error: null });
    sendWs({ type: "open", name });
  },

  select: (id) => set({ selected: id }),

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
    } catch (e) {
      set({ error: (e as Error).message, pendingSince: null });
      await get().load(b.name);
    }
  },

  onMessage: (msg) => {
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
  };
  ws.onmessage = (e) => useStore.getState().onMessage(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1000);
}
