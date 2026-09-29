import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { Decoration, EditorView } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useRef, useState } from "react";
import { api, type RecipeLocation } from "../api";

/** Read-only view of a recipe's source at its `def`, with "Open in editor". */
export function CodeView({ recipe, onClose }: { recipe: string; onClose: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [loc, setLoc] = useState<RecipeLocation | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api.locate(recipe).then(setLoc, (e) => setMsg((e as Error).message));
  }, [recipe]);

  useEffect(() => {
    if (!loc || !host.current) return;
    const doc = EditorState.create({ doc: loc.text }).doc;
    const line = doc.line(Math.min(loc.line, doc.lines));
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc,
        selection: { anchor: line.from },
        extensions: [basicSetup, python(), EditorState.readOnly.of(true), EditorView.editable.of(false),
          EditorView.decorations.of(Decoration.set([Decoration.line({ class: "cm-def-line" }).range(line.from)]))],
      }),
    });
    view.dispatch({ effects: EditorView.scrollIntoView(line.from, { y: "start", yMargin: 40 }) });
    return () => view.destroy();
  }, [loc]);

  const open = async () => {
    try {
      const r = await api.openRecipe(recipe);
      setMsg(r.opened ? `Opened with: ${r.command}` : "No editor on the backend (no `code`, $VISUAL or $EDITOR); use this view.");
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <div className="modal" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} data-testid="code-view">
        <div className="row">
          <strong>{loc ? `${loc.file}:${loc.line}` : recipe}</strong>
          <button onClick={open} disabled={!loc}>Open in editor</button>
          <button onClick={onClose}>Close</button>
        </div>
        {msg && <div className="meta" data-testid="code-msg">{msg}</div>}
        <div className="cm" ref={host} />
      </div>
    </div>
  );
}
