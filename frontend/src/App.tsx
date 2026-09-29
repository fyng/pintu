import { DndContext, DragOverlay, PointerSensor, pointerWithin, rectIntersection, useSensor, useSensors, type CollisionDetection, type DragEndEvent } from "@dnd-kit/core";
import { useEffect, useState } from "react";
import { api, type Panel } from "./api";
import { Canvas, canvasCoords } from "./components/Canvas";
import { FileBrowser } from "./components/FileBrowser";
import { Gallery } from "./components/Gallery";
import { Inspector } from "./components/Inspector";
import { Preview } from "./components/Preview";
import { dropCell, pitch, rect } from "./geometry";
import { append, move, place, span } from "./multiples";
import { connect, useStore } from "./store";

/** The droppable under the pointer, so small inner-grid cells take drops precisely. */
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length ? hits : rectIntersection(args);
};

/** The value a linked gallery item gives a multiples panel's cell, if it matches the panel's recipe. */
function itemValue(p: Panel | undefined, recipe: string | null | undefined, params: Record<string, unknown>): string | null {
  const m = p?.multiples;
  if (!m || !recipe || recipe !== p.source.recipe || params[m.item] === undefined) return null;
  return String(params[m.item]);
}

export function App() {
  const { board, boards, error, warnings } = useStore();
  const load = useStore((s) => s.load);
  const commit = useStore((s) => s.commit);
  const [dragging, setDragging] = useState<string | null>(null);
  const [tab, setTab] = useState<"files" | "gallery">("gallery");
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  useEffect(() => {
    connect();
    api.project().then((p) => {
      useStore.setState({ boards: p.boards });
      const want = new URLSearchParams(location.search).get("board");
      const name = want && p.boards.includes(want) ? want : p.boards[0];
      if (name) load(name);
    });
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useStore.getState();
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if ((e.key === "Delete" || e.key === "Backspace") && s.selected) {
        commit([{ op: "remove", id: s.selected }]);
        s.select(null);
      } else if (e.key === "Escape") s.select(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [commit]);

  const onDragEnd = (e: DragEndEvent) => {
    setDragging(null);
    const b = useStore.getState().board;
    const data = e.active.data.current;
    const path = data?.path as string | undefined;
    const recipe = data?.recipe as string | null | undefined;
    const params = (data?.params ?? {}) as Record<string, unknown>;
    const start = e.activatorEvent as PointerEvent;
    const cell = e.over?.data.current as { panel: string; r: number; c: number } | undefined;
    if (b && cell && String(e.over?.id).startsWith("mcell:")) {
      // Inner grid of a multiples panel: move or swap, span, or fill from the gallery.
      const p = b.panels.find((q) => q.id === cell.panel);
      const m = p?.multiples?.mosaic;
      if (!m || (data?.panel && data.panel !== cell.panel)) return;
      const value = itemValue(p, recipe, params);
      const next = data?.mosaicItem ? move(m, data.mosaicItem, cell.r, cell.c)
        : data?.spanItem ? span(m, data.spanItem, cell.r, cell.c)
        : value !== null ? place(m, value, cell.r, cell.c) : m;
      if (next !== m) commit([{ op: "set_multiples", id: cell.panel, mosaic: next }]);
      return;
    }
    if (!b || !path || e.over?.id !== "canvas" || !canvasCoords.toMm) return;
    const mm = canvasCoords.toMm(start.clientX + e.delta.x, start.clientY + e.delta.y);
    if (!mm) return;
    const [x, y] = mm;
    const hit = b.panels.find((p) => {
      const [px, py, w, h] = rect(b.page, p.cell);
      return x >= px && x <= px + w && y >= py && y <= py + h;
    });
    const value = itemValue(hit, recipe, params);
    // A matching item dropped on a multiples panel joins its mosaic.
    if (hit?.multiples && value !== null) return void commit([{ op: "set_multiples", id: hit.id, mosaic: append(hit.multiples.mosaic, value) }]);
    if (hit) return void commit([recipe ? { op: "set_recipe", id: hit.id, recipe, params } : { op: "set_source", id: hit.id, file: path }]);
    const ux = Math.floor(x / pitch(b.page.width, b.page.grid[0], b.page.gutter));
    const uy = Math.floor(y / pitch(b.page.height, b.page.grid[1], b.page.gutter));
    const free = dropCell(b.page, b.panels.map((p) => p.cell), ux, uy);
    // Linked gallery items become recipe panels, which re-render at the cell size.
    if (free) commit([recipe ? { op: "add", cell: free, recipe, params } : { op: "add", cell: free, file: path }]);
  };

  const newBoard = async () => {
    const name = prompt("Board name");
    if (!name) return;
    try {
      await api.newBoard(name);
      useStore.setState({ boards: [...boards, name].sort() });
      await load(name);
    } catch (err) {
      useStore.setState({ error: (err as Error).message });
    }
  };

  return (
    <DndContext sensors={sensors} collisionDetection={collision} onDragStart={(e) => setDragging(String(e.active.data.current?.label ?? e.active.data.current?.path))} onDragEnd={onDragEnd}
      onDragCancel={() => setDragging(null)}>
      <div className="app">
        <header>
          <strong>pintu</strong>
          <select value={board?.name ?? ""} onChange={(e) => load(e.target.value)}>
            {boards.map((b) => <option key={b}>{b}</option>)}
          </select>
          <button onClick={newBoard}>New board</button>
          {error && <span className="error" data-testid="error">{error}</span>}
          {[...(board?.warnings ?? []), ...warnings].map((w, i) => <span key={i} className="warn">{w}</span>)}
        </header>
        <aside>
          <div className="tabs">
            <button className={tab === "gallery" ? "on" : ""} onClick={() => setTab("gallery")}>Gallery</button>
            <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>Files</button>
          </div>
          {tab === "gallery" ? <Gallery /> : <FileBrowser />}
        </aside>
        <main>{board ? <Canvas /> : <p>No board. Create one.</p>}</main>
        <section className="side">
          {board && <Inspector />}
          <Preview />
        </section>
      </div>
      <DragOverlay>{dragging && <div className="drag-chip">{dragging}</div>}</DragOverlay>
    </DndContext>
  );
}
