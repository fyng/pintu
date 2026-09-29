import { useState } from "react";
import { splitExact } from "../geometry";
import { useStore } from "../store";

/** Settings of the selected panel, or of the page when none is selected. */
export function Inspector() {
  const board = useStore((s) => s.board)!;
  const selected = useStore((s) => s.selected);
  const commit = useStore((s) => s.commit);
  const select = useStore((s) => s.select);
  const [n, setN] = useState(2);
  const [axis, setAxis] = useState<"x" | "y">("x");
  const p = board.panels.find((q) => q.id === selected);

  if (!p) {
    const pg = board.page;
    return (
      <div className="inspector">
        <h3>Page</h3>
        <label>
          Width
          <select value={pg.width} onChange={(e) => commit([{ op: "set_page", width: Number(e.target.value) }])}>
            {Object.entries(board.preset.widths).map(([k, v]) => <option key={k} value={v}>{`${k} (${v} mm)`}</option>)}
            {!Object.values(board.preset.widths).includes(pg.width) && <option value={pg.width}>{pg.width} mm</option>}
          </select>
        </label>
        <label>
          Height (mm)
          <input type="number" defaultValue={pg.height} key={`h${pg.height}`}
            onBlur={(e) => Number(e.target.value) !== pg.height && commit([{ op: "set_page", height: Number(e.target.value) }])} />
        </label>
        <div className="meta">Grid {pg.grid[0]} × {pg.grid[1]}, gutter {pg.gutter} mm, style {pg.style}</div>
      </div>
    );
  }

  const units = axis === "x" ? p.cell[2] - p.cell[0] : p.cell[3] - p.cell[1];
  const setting = p.letterSetting;
  return (
    <div className="inspector" data-testid="inspector">
      <h3>{p.id}</h3>
      <div className="meta">cell [{p.cell.join(", ")}] · {p.rect[2].toFixed(1)} × {p.rect[3].toFixed(1)} mm</div>
      <div className="meta">{p.source.file ?? p.source.recipe ?? "no source"}</div>
      <label>
        Letter
        <select value={setting === "auto" ? "auto" : setting === null ? "none" : "fixed"}
          onChange={(e) => {
            const v = e.target.value;
            commit([{ op: "set_letter", id: p.id, letter: v === "auto" ? "auto" : v === "none" ? null : p.letter ?? "a" }]);
          }}>
          <option value="auto">auto ({p.letter ?? "–"})</option>
          <option value="none">none</option>
          <option value="fixed">fixed</option>
        </select>
        {setting !== "auto" && setting !== null && (
          <input className="letter-input" defaultValue={setting} key={setting} maxLength={3}
            onBlur={(e) => e.target.value && e.target.value !== setting && commit([{ op: "set_letter", id: p.id, letter: e.target.value }])} />
        )}
      </label>
      <div className="split">
        Split into
        <input type="number" min={2} max={units} value={n} onChange={(e) => setN(Number(e.target.value))} />
        <select value={axis} onChange={(e) => setAxis(e.target.value as "x" | "y")}>
          <option value="x">columns</option>
          <option value="y">rows</option>
        </select>
        <button disabled={n < 2 || n > units} onClick={() => commit([{ op: "split", id: p.id, n, axis }])}>Split</button>
        {n >= 2 && !splitExact(units, n) && <div className="warn">{units} units do not split into {n} equal parts; parts will differ by one unit.</div>}
      </div>
      <div className="row">
        {p.source.file && <button onClick={() => commit([{ op: "set_source", id: p.id, file: null }])}>Clear source</button>}
        <button onClick={() => { select(null); commit([{ op: "remove", id: p.id }]); }}>Delete</button>
      </div>
    </div>
  );
}
