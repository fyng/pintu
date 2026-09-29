import { useDraggable, useDroppable } from "@dnd-kit/core";
import { useState } from "react";
import type { Panel, ShareMode } from "../api";
import { remove, reshape, shape, spans, type Mosaic } from "../multiples";
import { useStore } from "../store";

const MODES: ShareMode[] = ["all", "row", "col", "none"];

/** Droppable id of an inner-grid cell; `App` routes drops on it to mosaic edits. */
export const cellId = (panel: string, r: number, c: number) => `mcell:${panel}:${r}:${c}`;

function Cell({ panel, r, c }: { panel: string; r: number; c: number }) {
  const { setNodeRef, isOver } = useDroppable({ id: cellId(panel, r, c), data: { panel, r, c } });
  return <div ref={setNodeRef} className={`mcell ${isOver ? "over" : ""}`} style={{ gridRow: r + 1, gridColumn: c + 1 }}
    data-testid="mosaic-cell" data-cell={`${r},${c}`} />;
}

function Item({ panel, v, s, onRemove }: { panel: string; v: string; s: { r0: number; c0: number; r1: number; c1: number }; onRemove: () => void }) {
  const body = useDraggable({ id: `mitem:${panel}:${v}`, data: { mosaicItem: v, panel, label: v } });
  const handle = useDraggable({ id: `mspan:${panel}:${v}`, data: { spanItem: v, panel, label: `span ${v}` } });
  return (
    <div ref={body.setNodeRef} {...body.listeners} {...body.attributes} data-testid="mosaic-item" data-item={v}
      className={`mitem ${body.isDragging ? "dragging" : ""}`}
      style={{ gridRow: `${s.r0 + 1} / ${s.r1 + 1}`, gridColumn: `${s.c0 + 1} / ${s.c1 + 1}` }}
      title="Drag onto a cell to move or swap; drag the corner to span">
      <span>{v}</span>
      <button className="mremove" onPointerDown={(e) => e.stopPropagation()} onClick={onRemove} title="Remove">×</button>
      <span ref={handle.setNodeRef} {...handle.listeners} {...handle.attributes} className="mspan" data-testid="mosaic-span" />
    </div>
  );
}

/**
 * A multiples panel's inner grid: rows × columns, share modes, items to drag
 * (move, swap, span) and cells that take gallery drops; each edit rewrites `mosaic`.
 */
export function MultiplesEditor({ p }: { p: Panel }) {
  const commit = useStore((s) => s.commit);
  const m = p.multiples!;
  const [rows, cols] = shape(m.mosaic);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const setMosaic = (mosaic: Mosaic) => commit([{ op: "set_multiples", id: p.id, mosaic }]);
  const items = [...spans(m.mosaic)];
  const reflowKey = `${JSON.stringify(m.mosaic)}@${m.cellMm.join("x")}`;
  const cells = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) cells.push(<Cell key={`${r},${c}`} panel={p.id} r={r} c={c} />);

  return (
    <div className="multiples" data-testid="multiples">
      <div className="row">
        Grid
        <input type="number" min={1} max={12} value={rows} data-testid="mosaic-rows"
          onChange={(e) => Number(e.target.value) >= 1 && setMosaic(reshape(m.mosaic, Number(e.target.value), cols))} />
        ×
        <input type="number" min={1} max={12} value={cols} data-testid="mosaic-cols"
          onChange={(e) => Number(e.target.value) >= 1 && setMosaic(reshape(m.mosaic, rows, Number(e.target.value)))} />
        {(["x", "y"] as const).map((k) => (
          <label key={k}>
            share {k}
            <select value={m.share[k]} onChange={(e) => commit([{ op: "set_multiples", id: p.id, share: { ...m.share, [k]: e.target.value } }])}>
              {MODES.map((v) => <option key={v}>{v}</option>)}
            </select>
          </label>
        ))}
      </div>
      <div className="mgrid" data-testid="mosaic"
        style={{
          gridTemplateColumns: (m.width_ratios ?? Array(cols).fill(1)).map((x) => `${x}fr`).join(" "),
          gridTemplateRows: `repeat(${rows}, 28px)`,
        }}>
        {cells}
        {items.map(([v, s]) => <Item key={v} panel={p.id} v={v} s={s} onRemove={() => setMosaic(remove(m.mosaic, v))} />)}
      </div>
      <div className="meta">
        {m.item} · cell {m.cellMm[0].toFixed(1)} × {m.cellMm[1].toFixed(1)} mm
        {m.minCell && ` (min ${m.minCell[0]} × ${m.minCell[1]})`} · drop linked {m.item} items into cells
      </div>
      {m.reflow && dismissed !== reflowKey && (
        <div className="warn reflow" data-testid="reflow">
          Cells are below the minimum size. Reflow {rows} × {cols} → {m.reflow.length} × {m.reflow[0].length}?
          <button onClick={() => setMosaic(m.reflow!)}>Reflow</button>
          <button onClick={() => setDismissed(reflowKey)}>Keep</button>
        </div>
      )}
    </div>
  );
}
