import { useDraggable } from "@dnd-kit/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { galleryQuery, itemLabel, parseValues, toggleValue, type Facet, type Filters, type GalleryItem, type GalleryPage, type Group } from "../gallery";
import { sessionsApi, useSessions, type PromoteSource } from "../sessions";
import { useStore } from "../store";

const PAGE = 60;
/** Facets with at most this many values show as chips; larger ones take typed values. */
const CHIPS = 12;

function Thumb({ item, picked, onPick }: { item: GalleryItem; picked: boolean; onPick: () => void }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `gallery:${item.path}`,
    data: { path: item.path, recipe: item.recipe, params: item.params, label: itemLabel(item) },
  });
  const size = item.width_mm && item.height_mm ? ` · ${item.width_mm.toFixed(0)} × ${item.height_mm.toFixed(0)} mm` : "";
  return (
    <figure ref={setNodeRef} {...listeners} {...attributes} onClick={onPick}
      className={`thumb ${isDragging ? "dragging" : ""} ${item.recipe ? "linked" : ""} ${picked ? "picked" : ""}`}
      title={`${item.path}${size}${item.recipe ? `\n${item.recipe}` : ""}\nDrag onto the board; click for Promote to recipe`}
      data-testid="gallery-item" data-path={item.path}>
      <img src={api.galleryThumbUrl(item.path, item.version)} loading="lazy" alt="" draggable={false} />
      <figcaption>{itemLabel(item)}</figcaption>
    </figure>
  );
}

function FacetFilter({ name, values, chosen, onChange }: { name: string; values: Facet[]; chosen: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState(chosen.join(", "));
  const joined = chosen.join(", ");
  useEffect(() => setText(joined), [joined]);
  if (values.length <= CHIPS) {
    return (
      <div className="facet">
        <span>{name}</span>
        {values.map((v) => (
          <button key={v.value} className={chosen.includes(v.value) ? "on" : ""}
            onClick={() => onChange(toggleValue({ [name]: chosen }, name, v.value)[name])}>
            {v.value} <small>{v.count}</small>
          </button>
        ))}
      </div>
    );
  }
  const list = `facet-${name}`;
  return (
    <div className="facet">
      <span>{name}</span>
      <input list={list} value={text} placeholder={`${values.length} values, e.g. ${values[0].value}`}
        onChange={(e) => setText(e.target.value)} onBlur={() => onChange(parseValues(text))}
        onKeyDown={(e) => e.key === "Enter" && onChange(parseValues(text))} />
      <datalist id={list}>{values.map((v) => <option key={v.value} value={v.value} />)}</datalist>
    </div>
  );
}

/** Thumbnail grid of the catalog, grouped by recipe, with param filters; items drag onto the board. */
export function Gallery() {
  const galleryRev = useStore((s) => s.galleryRev);
  const [group, setGroup] = useState<Group>(null);
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState<Filters>({});
  const [facets, setFacets] = useState<Record<string, Facet[]>>({});
  const [page, setPage] = useState<GalleryPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const loading = useRef(false);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    api.gallery(galleryQuery(group, q, filters, 0, PAGE)).then(
      (p) => { if (live) { setPage(p); setError(null); } },
      (e) => live && setError(e.message),
    );
    return () => { live = false; };
  }, [group, q, filters, galleryRev]);

  useEffect(() => {
    if (!group) return void setFacets({});
    api.facets(group).then((r) => setFacets(r.params), () => setFacets({}));
  }, [group, galleryRev]);

  const more = useCallback(async () => {
    if (!page || loading.current || page.items.length >= page.total) return;
    loading.current = true;
    try {
      const next = await api.gallery(galleryQuery(group, q, filters, page.items.length, PAGE));
      setPage((cur) => (cur && next.offset === cur.items.length ? { ...next, items: [...cur.items, ...next.items] } : cur));
    } finally {
      loading.current = false;
    }
  }, [page, group, q, filters]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const obs = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && more(), { rootMargin: "400px" });
    obs.observe(el);
    return () => obs.disconnect();
  }, [more]);

  const sections: { recipe: string | null; items: GalleryItem[] }[] = [];
  for (const it of page?.items ?? []) {
    const last = sections[sections.length - 1];
    if (last && last.recipe === it.recipe) last.items.push(it);
    else sections.push({ recipe: it.recipe, items: [it] });
  }
  const count = (r: string | null) => page?.groups.find((g) => g.recipe === r)?.count ?? 0;

  return (
    <div className="gallery" data-testid="gallery">
      <div className="gallery-bar">
        <select value={group === null ? "*" : group} onChange={(e) => {
          setFilters({});
          setGroup(e.target.value === "*" ? null : e.target.value);
        }}>
          <option value="*">All ({page?.groups.reduce((n, g) => n + g.count, 0) ?? 0})</option>
          {page?.groups.filter((g) => g.recipe).map((g) => <option key={g.recipe} value={g.recipe!}>{g.recipe} ({g.count})</option>)}
          {count(null) > 0 && <option value="">Unlinked files ({count(null)})</option>}
        </select>
        <input type="search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {Object.entries(facets).map(([k, vs]) => (
        <FacetFilter key={k} name={k} values={vs} chosen={filters[k] ?? []} onChange={(v) => setFilters({ ...filters, [k]: v })} />
      ))}
      {error && <div className="error">{error}</div>}
      {page && <div className="meta">{page.total} items</div>}
      {picked && <Pick path={picked} onClose={() => setPicked(null)} />}
      {sections.map((s, i) => (
        <section key={`${s.recipe}-${i}`}>
          <h4>{s.recipe ?? "Unlinked files"}</h4>
          <div className="thumbs">{s.items.map((it) => (
            <Thumb key={it.path} item={it} picked={it.path === picked} onPick={() => setPicked(it.path === picked ? null : it.path)} />
          ))}</div>
        </section>
      ))}
      <div ref={sentinel} />
    </div>
  );
}

/** The clicked gallery item: "Promote to recipe" when its script is known. */
function Pick({ path, onClose }: { path: string; onClose: () => void }) {
  const [src, setSrc] = useState<PromoteSource | null>(null);
  const [why, setWhy] = useState<string | null>(null);
  const llm = useSessions((s) => s.llm);
  useEffect(() => {
    setSrc(null);
    setWhy(null);
    sessionsApi.promoteSource(path).then(setSrc, (e) => setWhy(e.message));
  }, [path]);
  const board = useStore((s) => s.board?.name ?? null);
  return (
    <div className="gallery-pick" data-testid="gallery-pick">
      <div className="row"><b>{path}</b><button className="link" onClick={onClose}>close</button></div>
      {src ? (
        <div className="row">
          <span className="meta">script {src.script} → {src.suggestedRecipe}</span>
          <button data-testid="promote" disabled={llm?.configured === false}
            onClick={() => useSessions.getState().create({ kind: "promote", path, board })}>Promote to recipe</button>
        </div>
      ) : why && <div className="meta" data-testid="promote-unknown">Cannot promote: {why}</div>}
    </div>
  );
}
