import { useDroppable } from "@dnd-kit/core";
import { useEffect, useRef, useState } from "react";
import { api, type Panel, type RenderStatus } from "../api";
import { coarseLines, dragCell, lineAt, overlaps, rect, sameCell, type Cell, type Handle } from "../geometry";
import { useStore } from "../store";

const MARGIN = 6;
const EDGE = 1.5;

/** Converts client coordinates to page mm; set while the canvas is mounted. */
export const canvasCoords: { toMm: ((x: number, y: number) => [number, number] | null) | null } = { toMm: null };

interface Drag {
  id: string;
  handle: Handle;
  start: [number, number];
  from: Cell;
  cell: Cell;
  ok: boolean;
}

export function Canvas() {
  const board = useStore((s) => s.board)!;
  const selected = useStore((s) => s.selected);
  const select = useStore((s) => s.select);
  const toggleSelect = useStore((s) => s.toggleSelect);
  const selection = useStore((s) => s.selection);
  const commit = useStore((s) => s.commit);
  const setCellLocal = useStore((s) => s.setCellLocal);
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const { setNodeRef, isOver } = useDroppable({ id: "canvas" });
  const page = board.page;
  const [nx, ny] = page.grid;

  const toMm = (x: number, y: number): [number, number] | null => {
    const svg = svgRef.current;
    const m = svg?.getScreenCTM();
    if (!svg || !m) return null;
    const pt = new DOMPoint(x, y).matrixTransform(m.inverse());
    return [pt.x, pt.y];
  };
  useEffect(() => {
    canvasCoords.toMm = toMm;
    return () => {
      canvasCoords.toMm = null;
    };
  });

  const down = (e: React.PointerEvent, p: Panel, handle: Handle) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    if (e.shiftKey) return toggleSelect(p.id);
    const at = toMm(e.clientX, e.clientY);
    if (!at) return;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    select(p.id);
    setDrag({ id: p.id, handle, start: at, from: p.cell, cell: p.cell, ok: true });
  };
  const move = (e: React.PointerEvent) => {
    if (!drag) return;
    const at = toMm(e.clientX, e.clientY);
    if (!at) return;
    const cell = dragCell(page, drag.from, drag.handle, drag.start, at);
    if (sameCell(cell, drag.cell)) return;
    const ok = !board.panels.some((q) => q.id !== drag.id && overlaps(q.cell, cell));
    setDrag({ ...drag, cell, ok });
  };
  const up = () => {
    if (!drag) return;
    setDrag(null);
    if (drag.ok && !sameCell(drag.cell, drag.from)) {
      setCellLocal(drag.id, drag.cell);
      commit([{ op: "set_cell", id: drag.id, cell: drag.cell }]);
    }
  };

  const vline = (i: number, cls: string) => {
    const x = i === nx ? page.width : lineAt(page.width, nx, page.gutter, i) - (i ? page.gutter / 2 : 0);
    return <line key={`v${cls}${i}`} className={cls} x1={x} x2={x} y1={0} y2={page.height} />;
  };
  const hline = (i: number, cls: string) => {
    const y = i === ny ? page.height : lineAt(page.height, ny, page.gutter, i) - (i ? page.gutter / 2 : 0);
    return <line key={`h${cls}${i}`} className={cls} x1={0} x2={page.width} y1={y} y2={y} />;
  };
  const range = (n: number) => Array.from({ length: n - 1 }, (_, i) => i + 1);

  return (
    <div ref={setNodeRef} className={`canvas ${isOver ? "drop-over" : ""}`}>
      <svg
        ref={svgRef}
        data-testid="canvas"
        viewBox={`${-MARGIN} ${-MARGIN} ${page.width + 2 * MARGIN} ${page.height + 2 * MARGIN}`}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={() => setDrag(null)}
        onPointerDown={() => select(null)}
      >
        <rect className="page" x={0} y={0} width={page.width} height={page.height} />
        {drag && range(nx).map((i) => vline(i, "fine"))}
        {drag && range(ny).map((i) => hline(i, "fine"))}
        {coarseLines(nx).map((i) => vline(i, "coarse"))}
        {coarseLines(ny).map((i) => hline(i, "coarse"))}
        {board.panels.map((p) => {
          const cell = drag?.id === p.id ? drag.cell : p.cell;
          const [x, y, w, h] = rect(page, cell);
          const cls = ["panel", (p.id === selected || selection.includes(p.id)) && "selected", drag?.id === p.id && (drag.ok ? "dragging" : "invalid")]
            .filter(Boolean)
            .join(" ");
          const top = p.letter ? Math.min(board.preset.letterBand, h / 2) : 0;
          return (
            <g key={p.id} className={cls} data-panel={p.id}>
              <svg x={x} y={y} width={w} height={h} overflow="hidden">
                <rect className="panel-bg" width={w} height={h} />
                {p.file ? (
                  <image href={api.thumbUrl(p.file, p.fileVersion)} y={top} width={w} height={Math.max(0, h - top)} preserveAspectRatio="xMidYMid meet" />
                ) : (
                  <text className="placeholder" x={1} y={top + 3}>
                    {p.source.recipe ? `recipe ${p.source.recipe}` : p.source.file ? `missing ${p.source.file}` : p.id}
                  </text>
                )}
                {p.letter && (
                  <text className="letter" x={0.2} y={3}>
                    {p.letter}
                  </text>
                )}
              </svg>
              {p.render && (p.render.status !== "ok" || p.render.lint?.length) && <RenderBadge x={x + w} y={y} status={p.render} />}
              <rect className="body" x={x} y={y} width={w} height={h} onPointerDown={(e) => down(e, p, "move")} />
              {(["n", "s", "e", "w", "ne", "nw", "se", "sw"] as Handle[]).map((hd) => {
                const hx = hd.includes("w") ? x - EDGE / 2 : hd.includes("e") ? x + w - EDGE / 2 : x + EDGE / 2;
                const hy = hd.includes("n") ? y - EDGE / 2 : hd.includes("s") ? y + h - EDGE / 2 : y + EDGE / 2;
                const hw = hd === "n" || hd === "s" ? w - EDGE : EDGE;
                const hh = hd === "e" || hd === "w" ? h - EDGE : EDGE;
                return (
                  <rect key={hd} className={`handle h-${hd}`} data-handle={hd} x={hx} y={hy} width={hw} height={hh}
                    onPointerDown={(e) => down(e, p, hd)} />
                );
              })}
              {drag?.id === p.id && (
                <text className="size" x={x + w - 1} y={y + h - 1}>
                  {`${w.toFixed(1)} × ${h.toFixed(1)} mm`}
                </text>
              )}
            </g>
          );
        })}
        {board.groups.map((g) => {
          const [x, y, w, h] = rect(page, g.cell);
          return <rect key={g.id} className="group-outline" data-group={g.id} x={x - 0.8} y={y - 0.8} width={w + 1.6} height={h + 1.6} />;
        })}
      </svg>
    </div>
  );
}

const BADGE: Record<string, [string, string]> = {
  rendering: ["rendering…", "badge-rendering"],
  error: ["render error", "badge-error"],
  missing: ["missing recipe", "badge-missing"],
};

/** Badge label, class and tooltip: the render state, or the lint count of a good render. */
function badge(status: RenderStatus): [string, string, string] {
  if (status.status === "ok") {
    const n = status.lint?.length ?? 0;
    return [`lint: ${n}`, "badge-lint", (status.lint ?? []).map((i) => `${i.rule}: ${i.message}`).join("\n")];
  }
  const [label, cls] = BADGE[status.status];
  return [label, cls, status.error ?? label];
}

/** Render state of a recipe panel, at its top-right corner. */
function RenderBadge({ x, y, status }: { x: number; y: number; status: RenderStatus }) {
  const [label, cls, tip] = badge(status);
  const bw = label.length * 1.35 + 2;
  return (
    <g className={`badge ${cls}`} data-testid={status.status === "ok" ? "lint-badge" : "render-badge"} data-status={status.status}>
      <title>{tip}</title>
      <rect x={x - bw - 0.5} y={y + 0.5} width={bw} height={3.4} rx={0.8} />
      <text x={x - bw / 2 - 0.5} y={y + 2.9}>{label}</text>
    </g>
  );
}
