import { lazy, Suspense, useState } from "react";
import type { Panel } from "../api";
import { splitExact } from "../geometry";
import { useStore } from "../store";
import { MultiplesEditor } from "./MultiplesEditor";

const CodeView = lazy(() => import("./CodeView").then((m) => ({ default: m.CodeView })));

/** Settings of the selected panel, or of the page when none is selected. */
export function Inspector() {
  const board = useStore((s) => s.board)!;
  const selected = useStore((s) => s.selected);
  const commit = useStore((s) => s.commit);
  const select = useStore((s) => s.select);
  const selection = useStore((s) => s.selection);
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

  if (selection.length > 1) {
    return (
      <div className="inspector" data-testid="inspector">
        <h3>{selection.length} panels</h3>
        <div className="meta">{selection.join(", ")}</div>
        <div className="row">
          <button data-testid="group" onClick={() => commit([{ op: "group", ids: selection }])}>Group under one letter</button>
        </div>
      </div>
    );
  }

  const units = axis === "x" ? p.cell[2] - p.cell[0] : p.cell[3] - p.cell[1];
  const setting = p.letterSetting;
  const group = board.groups.find((g) => g.id === p.group);
  const letter = group ? group.letter : p.letter;
  return (
    <div className="inspector" data-testid="inspector">
      <h3>{p.id}</h3>
      {group && (
        <div className="row meta" data-testid="group-info">
          In group {group.id} ({group.panels.join(", ")}); the letter is the group's.
          <button onClick={() => commit([{ op: "ungroup", group: group.id }])}>Ungroup</button>
        </div>
      )}
      <div className="meta">cell [{p.cell.join(", ")}] · {p.rect[2].toFixed(1)} × {p.rect[3].toFixed(1)} mm
        {p.source.recipe && p.band > 0 && <> · plot {p.rect[2].toFixed(1)} × {(p.rect[3] - p.band).toFixed(1)} mm</>}</div>
      <div className="meta">{p.source.file ?? p.source.recipe ?? "no source"}</div>
      <label>
        Letter
        <select value={setting === "auto" ? "auto" : setting === null ? "none" : "fixed"}
          onChange={(e) => {
            const v = e.target.value;
            commit([{ op: "set_letter", id: p.id, letter: v === "auto" ? "auto" : v === "none" ? null : letter ?? "a" }]);
          }}>
          <option value="auto">auto ({letter ?? "–"})</option>
          <option value="none">none</option>
          <option value="fixed">fixed</option>
        </select>
        {setting !== "auto" && setting !== null && (
          <input className="letter-input" defaultValue={setting} key={setting} maxLength={3}
            onBlur={(e) => e.target.value && e.target.value !== setting && commit([{ op: "set_letter", id: p.id, letter: e.target.value }])} />
        )}
      </label>
      <RecipeSection p={p} />
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

/** A panel's recipe (`module:function`), its params as JSON, render state and "Open code". */
function RecipeSection({ p }: { p: Panel }) {
  const commit = useStore((s) => s.commit);
  const [code, setCode] = useState(false);
  const [bad, setBad] = useState<string | null>(null);
  const recipe = p.source.recipe ?? "";
  const params = JSON.stringify(p.params ?? {}, null, 1);
  const r = p.render;

  const setRecipe = (v: string) => v && v !== recipe && commit([{ op: "set_recipe", id: p.id, recipe: v }]);
  const setParams = (text: string) => {
    let v: unknown;
    try {
      v = JSON.parse(text || "{}");
    } catch (e) {
      return setBad((e as Error).message);
    }
    if (typeof v !== "object" || v === null || Array.isArray(v)) return setBad("params must be a JSON object");
    setBad(null);
    if (JSON.stringify(v) !== JSON.stringify(p.params ?? {})) commit([{ op: "set_recipe", id: p.id, recipe, params: v }]);
  };

  return (
    <div className="recipe" data-testid="recipe">
      <label>
        Recipe
        <input placeholder="module:function" defaultValue={recipe} key={`r${p.id}${recipe}`} data-testid="recipe-input"
          onBlur={(e) => setRecipe(e.target.value.trim())} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
      </label>
      {recipe && (
        <>
          <textarea rows={3} defaultValue={params} key={`p${p.id}${params}`} data-testid="params-input" spellCheck={false}
            onBlur={(e) => setParams(e.target.value)} />
          {bad && <div className="warn">{bad}</div>}
          {p.multiples ? <MultiplesEditor p={p} />
            : p.multiplesItem && p.params && p.multiplesItem in p.params && (
              <button data-testid="make-multiples" onClick={() => commit([{ op: "set_multiples", id: p.id, item: p.multiplesItem }])}>
                Make multiples of {p.multiplesItem}
              </button>
            )}
          <div className="row">
            <span className="meta" data-testid="render-status">
              {r?.status === "ok" ? `rendered${r.cached ? " (cache)" : ""}${r.seconds !== undefined ? ` in ${r.seconds.toFixed(2)} s` : ""}`
                : r?.status === "missing" ? "missing recipe: showing the last render" : r?.status ?? ""}
            </span>
            <button onClick={() => setCode(true)} disabled={r?.status === "missing"}>Open code</button>
          </div>
          {r?.status === "ok" && !!r.lint?.length && (
            <ul className="lint" data-testid="lint">
              {r.lint.map((i, k) => <li key={k}><b>{i.rule}</b> {i.message}</li>)}
            </ul>
          )}
          {r?.status === "error" && <pre className="render-error">{[r.error, r.stderr].filter(Boolean).join("\n")}</pre>}
          {code && <Suspense fallback={null}><CodeView recipe={recipe} onClose={() => setCode(false)} /></Suspense>}
        </>
      )}
    </div>
  );
}
