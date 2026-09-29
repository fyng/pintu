import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

/** The Typst-compiled board, as pushed by the backend. */
export function Preview() {
  const preview = useStore((s) => s.preview);
  const latency = useStore((s) => s.latency);
  const name = useStore((s) => s.board?.name);
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!preview) return;
    const u = URL.createObjectURL(new Blob([preview.svg], { type: "image/svg+xml" }));
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [preview]);

  return (
    <div className="preview">
      <div className="bar">
        Typst preview
        {preview && <span data-testid="preview-meta" data-rev={preview.rev}> · rev {preview.rev} · compile {preview.ms.toFixed(1)} ms{latency !== null && ` · edit→preview ${latency.toFixed(0)} ms`}</span>}
        {name && <a href={api.pdfUrl(name)} target="_blank" rel="noreferrer">PDF</a>}
      </div>
      {url && <img data-testid="preview" src={url} alt="board preview" />}
    </div>
  );
}
