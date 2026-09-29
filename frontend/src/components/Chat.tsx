import { useEffect, useRef, useState } from "react";
import { dropCell } from "../geometry";
import { openTurns, statusLabel, tree, useSessions, type Part, type Session, type Status } from "../sessions";
import { useStore } from "../store";
import { DiffView } from "./DiffView";

const EMPTY: Part[] = [];

/** Idle, busy, or the retry attempt with a countdown to the next try. */
export function StatusBadge({ status }: { status: Status }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (status.type !== "retry") return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [status.type]);
  return (
    <span className={`st st-${status.type}`} data-testid="session-status" data-status={status.type}
      title={status.type === "retry" ? status.message : undefined}>
      {statusLabel(status)}
    </span>
  );
}

const busy = (s: Session) => s.status.type !== "idle";

/** The chat panel: the session list with a prompt box, or one open session. */
export function Chat() {
  const open = useSessions((s) => s.open);
  const llm = useSessions((s) => s.llm);
  const error = useSessions((s) => s.error);
  const notice = useSessions((s) => s.notice);
  return (
    <div className="chat" data-testid="chat">
      {llm && !llm.configured && (
        <div className="warn" data-testid="no-llm">
          No LLM profile is set up, so agent sessions cannot start ({llm.error}). Add one to llm.toml and start
          <code> pintu serve --profile NAME</code>.
        </div>
      )}
      {error && <div className="error" data-testid="chat-error">{error}</div>}
      {notice && <div className="meta" data-testid="chat-notice">{notice}</div>}
      {open ? <SessionView id={open} /> : <><PromptBox /><SessionList /></>}
    </div>
  );
}

/** Free-text prompt that starts a session, bound to the selected (or last selected) panel unless unticked. */
function PromptBox() {
  const board = useStore((s) => s.board);
  const selected = useStore((s) => s.selected);
  const last = useStore((s) => s.lastSelected);
  const llm = useSessions((s) => s.llm);
  const create = useSessions((s) => s.create);
  const [text, setText] = useState("");
  const [bind, setBind] = useState(true);
  const panel = board?.panels.find((p) => p.id === (selected ?? last)) ?? null;
  const target = bind ? panel : null;
  const send = async () => {
    if (!text.trim()) return;
    const s = await create({ kind: "prompt", board: board?.name ?? null, panel: target?.id ?? null, prompt: text });
    if (s) setText("");
  };
  return (
    <div className="prompt-box">
      <div className={`scope ${target ? "scope-panel" : ""}`} data-testid="prompt-scope" data-panel={target?.id ?? ""}>
        {panel ? (
          <label className="inline">
            <input type="checkbox" checked={bind} onChange={(e) => setBind(e.target.checked)} data-testid="prompt-bind" />
            About panel <b>{panel.id}</b>{panel.letter ? ` (${panel.letter})` : ""}, plot area{" "}
            {panel.rect[2].toFixed(1)} × {(panel.rect[3] - panel.band).toFixed(1)} mm
          </label>
        ) : null}
        {!target && <span className="meta">About the whole board{panel ? "" : "; click a panel to ask about its plot"}</span>}
      </div>
      <textarea rows={3} value={text} placeholder="Ask the agent to change a plot" data-testid="prompt-input"
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.metaKey || e.ctrlKey) && send()} />
      <div className="row">
        <span className="meta">The layout is yours: the agent changes plots, not cells.</span>
        <button onClick={send} disabled={!text.trim() || llm?.configured === false} data-testid="prompt-send">Send</button>
      </div>
    </div>
  );
}

function SessionRow({ s, child }: { s: Session; child?: boolean }) {
  const show = useSessions((st) => st.show);
  const activity = useSessions((st) => st.activity[s.id]);
  const n = openTurns(s).length;
  return (
    <li className={`session-row ${child ? "child" : ""}`} data-testid="session-row" data-session={s.id} data-kind={s.kind}
      onClick={() => show(s.id)}>
      <div className="row">
        <span className="kind">{s.kind}</span>
        <span className="title">{s.title}</span>
        <StatusBadge status={s.status} />
      </div>
      <div className="meta">
        {[s.panel && `${s.board}/${s.panel}`, n ? `${n} open turn${n > 1 ? "s" : ""}` : null, busy(s) ? activity : s.result?.stopped,
          s.error && "error"].filter(Boolean).join(" · ")}
      </div>
    </li>
  );
}

function SessionList() {
  const sessions = useSessions((s) => s.sessions);
  const items = tree(sessions);
  if (!items.length) return <p className="meta">No sessions yet.</p>;
  return (
    <ul className="sessions" data-testid="sessions">
      {items.map(({ session, children }) => (
        <li key={session.id}>
          <ul>
            <SessionRow s={session} />
            {children.map((c) => <SessionRow key={c.id} s={c} child />)}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/** The first free cell of the board, scanning rows from the top left. */
function freeCell() {
  const b = useStore.getState().board;
  if (!b) return null;
  const cells = b.panels.map((p) => p.cell);
  for (let y = 0; y < b.page.grid[1]; y++)
    for (let x = 0; x < b.page.grid[0]; x++) {
      const c = dropCell(b.page, cells, x, y);
      if (c) return c;
    }
  return null;
}

/** One session: its turns and parts, Stop, Accept and Revert, conflicts, trace and a follow-up prompt. */
function SessionView({ id }: { id: string }) {
  const s = useSessions((st) => st.sessions[id]);
  const parts = useSessions((st) => st.parts[id] ?? EMPTY);
  const sessions = useSessions((st) => st.sessions);
  const { show, abort, accept, revert, prompt } = useSessions.getState();
  const pending = useSessions((st) => (st.pendingRevert?.sessionId === id ? st.pendingRevert : null));
  const board = useStore((st) => st.board);
  const [trace, setTrace] = useState(false);
  const [text, setText] = useState("");
  const [placed, setPlaced] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [parts.length]);
  if (!s) return <p className="meta">Loading…</p>;

  const idle = !busy(s);
  const open = openTurns(s);
  const children = Object.values(sessions).filter((c) => c.parentId === id);
  const recipe = s.kind === "promote" ? s.result?.recipe : null;
  const place = async () => {
    const cell = freeCell();
    if (!recipe || !cell) return useSessions.setState({ error: cell ? "no recipe" : "the board has no free cell" });
    await useStore.getState().commit([{ op: "add", cell, recipe }]);
    setPlaced(`${recipe} placed at [${cell.join(", ")}]`);
  };
  const send = async () => {
    if (!text.trim()) return;
    await prompt(id, text);
    setText("");
  };

  return (
    <div className="session" data-testid="session" data-session={id}>
      <div className="row">
        <button onClick={() => show(null)} data-testid="session-back">← Sessions</button>
        <StatusBadge status={s.status} />
        {!idle && <button onClick={() => abort(id)} data-testid="session-stop">Stop</button>}
        <button className={trace ? "on" : ""} onClick={() => setTrace(!trace)} data-testid="session-trace">Trace</button>
      </div>
      <h3>{s.title}</h3>
      <div className="meta">
        {[s.kind, s.panel && `${s.board}/${s.panel}`, s.profile?.model, `${s.steps} steps`,
          s.usage.total ? `${s.usage.total} tokens` : null, s.result && `stopped: ${s.result.stopped}${s.result.seconds !== undefined ? ` in ${s.result.seconds.toFixed(0)} s` : ""}`]
          .filter(Boolean).join(" · ")}
      </div>
      {s.parentId && <button className="link" onClick={() => show(s.parentId)}>Parent session</button>}
      {children.length > 0 && (
        <ul className="sessions">{children.map((c) => <SessionRow key={c.id} s={c} child />)}</ul>
      )}
      {s.error && <div className="error">{s.error}</div>}
      {open.length > 0 && idle && (
        <div className="row">
          <button onClick={() => accept(id)} data-testid="accept-all">Accept all</button>
          <button onClick={() => revert(id)} data-testid="revert-all">Revert all</button>
        </div>
      )}
      {pending && (
        <div className="conflicts" data-testid="conflicts">
          <b>Revert conflicts:</b> these files changed after the agent's edit.
          <ul>{pending.conflicts.map((c) => <li key={`${c.turn}${c.path}`}>{c.path} (turn {c.turn}): {c.reason}</li>)}</ul>
          <div className="row">
            <button onClick={() => revert(id, pending.turn, "skip")} data-testid="revert-skip">Keep those, revert the rest</button>
            <button onClick={() => revert(id, pending.turn, "overwrite")} data-testid="revert-overwrite">Overwrite</button>
            <button onClick={() => useSessions.setState({ pendingRevert: null })}>Cancel</button>
          </div>
        </div>
      )}
      {recipe && board && (
        <div className="row">
          <span className="meta">New recipe <code>{recipe}</code></span>
          <button onClick={place} data-testid="place-recipe">Place on board</button>
          {placed && <span className="meta" data-testid="placed">{placed}</span>}
        </div>
      )}
      {trace ? (
        <pre className="trace" data-testid="trace">{JSON.stringify({ session: s, parts }, null, 1)}</pre>
      ) : (
        s.turns.map((t) => (
          <div key={t.n} className={`turn turn-${t.state}`} data-testid="turn" data-turn={t.n} data-state={t.state}>
            <div className="turn-head row">
              <b>Turn {t.n}</b>
              <span className="meta">{t.state}{t.outcome ? ` · ${t.outcome}` : ""}{t.kept?.length ? ` · kept ${t.kept.join(", ")}` : ""}</span>
              {t.state === "open" && idle && (
                <>
                  <button onClick={() => accept(id, t.n)} data-testid="turn-accept" title={`Accept turns up to ${t.n}`}>Accept</button>
                  <button onClick={() => revert(id, t.n)} data-testid="turn-revert" title={`Revert turns ${t.n} and later`}>
                    {t.n === open[open.length - 1] ? "Revert" : "Revert from here"}
                  </button>
                </>
              )}
            </div>
            {parts.filter((p) => p.turn === t.n).map((p) => <PartView key={p.id} p={p} />)}
          </div>
        ))
      )}
      <div ref={end} />
      <div className="prompt-box">
        <textarea rows={2} value={text} placeholder={idle ? "Follow up" : "Busy…"} data-testid="followup-input"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.metaKey || e.ctrlKey) && send()} />
        <button onClick={send} disabled={!idle || !text.trim()} data-testid="followup-send">Send</button>
      </div>
    </div>
  );
}

const json = (v: unknown) => JSON.stringify(v, null, 1);

function PartView({ p }: { p: Part }) {
  if (p.type === "text")
    return <div className={`msg msg-${p.role}`} data-testid={`text-${p.role}`}>{p.text}</div>;
  if (p.type === "reasoning")
    return <details className="reasoning" data-testid="reasoning"><summary>Reasoning</summary><div className="msg">{p.text}</div></details>;
  if (p.type === "patch")
    return (
      <div className="patch" data-testid="patch">
        {p.files.map((f) => <DiffView key={f.path} file={f} />)}
      </div>
    );
  const st = p.state;
  const secs = "time" in st && "end" in st.time ? ` ${(st.time.end - st.time.start).toFixed(1)} s` : "";
  return (
    <details className="tool" data-testid="tool-part" data-tool={p.tool} data-status={st.status}>
      <summary>
        <code>{p.tool}</code> <span className={`st st-${st.status}`}>{st.status}</span>{secs}
        {"image" in st && st.image && <span className="meta"> · image sent to the model</span>}
      </summary>
      <div className="meta">Input</div>
      <pre>{json(p.input)}</pre>
      {"output" in st && (
        <>
          <div className="meta">Output</div>
          <pre>{st.output}</pre>
        </>
      )}
    </details>
  );
}
