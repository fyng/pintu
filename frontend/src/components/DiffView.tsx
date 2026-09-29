import type { PatchFile } from "../sessions";

export interface DiffLine {
  kind: "add" | "del" | "ctx" | "hunk" | "meta";
  text: string;
  old: number | null;
  new: number | null;
}

/** Parses a unified diff into lines with old and new line numbers. */
export function parseDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  let o = 0;
  let n = 0;
  for (const line of diff.split("\n")) {
    const h = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (h) {
      o = Number(h[1]);
      n = Number(h[2]);
      out.push({ kind: "hunk", text: line, old: null, new: null });
    } else if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("\\")) {
      out.push({ kind: "meta", text: line, old: null, new: null });
    } else if (line.startsWith("+")) {
      out.push({ kind: "add", text: line.slice(1), old: null, new: n++ });
    } else if (line.startsWith("-")) {
      out.push({ kind: "del", text: line.slice(1), old: o++, new: null });
    } else if (line.startsWith(" ")) {
      out.push({ kind: "ctx", text: line.slice(1), old: o++, new: n++ });
    }
  }
  return out;
}

/** One file of a turn's patch as a unified diff with line numbers. */
export function DiffView({ file }: { file: PatchFile }) {
  const lines = parseDiff(file.diff).filter((l) => l.kind !== "meta");
  const add = lines.filter((l) => l.kind === "add").length;
  const del = lines.filter((l) => l.kind === "del").length;
  return (
    <details className="diff" open data-testid="diff" data-path={file.path}>
      <summary>
        <span className={`file-${file.status}`}>{file.status}</span> {file.path} <span className="diff-add">+{add}</span> <span className="diff-del">−{del}</span>
      </summary>
      <table>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i} className={`dl-${l.kind}`}>
              <td className="ln">{l.old ?? ""}</td>
              <td className="ln">{l.new ?? ""}</td>
              <td className="code">{l.kind === "add" ? "+" : l.kind === "del" ? "-" : l.kind === "ctx" ? " " : ""}{l.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
