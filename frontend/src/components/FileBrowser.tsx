import { useDraggable } from "@dnd-kit/core";
import { useEffect, useState } from "react";
import { api, type Entry } from "../api";

function FileItem({ e }: { e: Entry }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: `file:${e.path}`, data: { path: e.path } });
  return (
    <li ref={setNodeRef} {...listeners} {...attributes} className={`file ${isDragging ? "dragging" : ""}`} title={`Drag onto the board: ${e.path}`}>
      {e.name}
    </li>
  );
}

/** Project folder browser; image files drag onto the canvas. */
export function FileBrowser() {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.files(path).then((r) => { setEntries(r.entries); setError(null); }, (e) => setError(e.message));
  }, [path]);

  const parts = path ? path.split("/") : [];
  return (
    <div className="files">
      <div className="crumbs">
        <button onClick={() => setPath("")}>project</button>
        {parts.map((p, i) => (
          <span key={i}>
            /<button onClick={() => setPath(parts.slice(0, i + 1).join("/"))}>{p}</button>
          </span>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      <ul>
        {entries.map((e) =>
          e.dir ? (
            <li key={e.path} className="dir" onClick={() => setPath(e.path)}>{e.name}/</li>
          ) : e.kind ? (
            <FileItem key={e.path} e={e} />
          ) : (
            <li key={e.path} className="other">{e.name}</li>
          ),
        )}
      </ul>
    </div>
  );
}
