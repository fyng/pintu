import { beforeEach, describe, expect, test, vi } from "vitest";
import { parseDiff } from "./components/DiffView";
import { applyEvent, mergeParts, openTurns, statusLabel, tree, useSessions, type Live, type Part, type Session } from "./sessions";

const session = (id: string, extra: Partial<Session> = {}): Session => ({
  id, parentId: null, kind: "prompt", title: id, board: "fig", panel: "km", created: 1, updated: 1,
  status: { type: "idle" }, turns: [], size: null, oldSize: null, profile: null, error: null, result: null,
  usage: {}, steps: 0, ...extra,
});

const tool = (id: string, status: "pending" | "running" | "done", start = 1): Part => ({
  id, sessionId: "s1", turn: 1, time: { start }, type: "tool", callId: "c", tool: "edit_file", input: {}, step: 1,
  state: status === "pending" ? { status } : status === "running" ? { status, time: { start } }
    : { status, time: { start, end: start + 1 }, output: "ok", image: false },
});

const empty: Live = { sessions: {}, parts: {}, activity: {} };

describe("applyEvent", () => {
  test("session events upsert and set status", () => {
    let s = applyEvent(empty, { type: "session.created", session: session("s1") });
    expect(s.sessions.s1.status.type).toBe("idle");
    s = applyEvent(s, { type: "session.status", sessionId: "s1", status: { type: "retry", attempt: 2, message: "503", next: 10 } });
    expect(s.sessions.s1.status).toEqual({ type: "retry", attempt: 2, message: "503", next: 10 });
    expect(applyEvent(s, { type: "session.status", sessionId: "nope", status: { type: "busy" } })).toBe(s);
    s = applyEvent(s, { type: "session.updated", session: session("s1", { steps: 3 }) });
    expect(s.sessions.s1.steps).toBe(3);
  });

  test("parts are kept only for open sessions; tool parts move forward", () => {
    let s = applyEvent(empty, { type: "part.added", sessionId: "s1", part: tool("p1", "pending") });
    expect(s.parts.s1).toBeUndefined();
    expect(s.activity.s1).toBe("edit_file pending");
    s = { ...s, parts: { s1: [] } };
    s = applyEvent(s, { type: "part.added", sessionId: "s1", part: tool("p1", "pending") });
    s = applyEvent(s, { type: "part.updated", sessionId: "s1", part: tool("p1", "done") });
    s = applyEvent(s, { type: "part.updated", sessionId: "s1", part: tool("p1", "running") }); // late, stale
    expect(s.parts.s1).toHaveLength(1);
    expect((s.parts.s1[0] as any).state.status).toBe("done");
  });

  test("other messages leave the state as is", () => {
    expect(applyEvent(empty, { type: "preview" })).toBe(empty);
  });
});

test("mergeParts: fetched and live parts by id, newest state, in time order", () => {
  const merged = mergeParts([tool("a", "running", 1), tool("b", "done", 3)], [tool("a", "done", 1), tool("c", "pending", 2)]);
  expect(merged.map((p) => p.id)).toEqual(["a", "c", "b"]);
  expect((merged[0] as any).state.status).toBe("done");
});

test("statusLabel shows the retry attempt and countdown", () => {
  expect(statusLabel({ type: "idle" })).toBe("idle");
  expect(statusLabel({ type: "busy" })).toBe("busy");
  expect(statusLabel({ type: "retry", attempt: 1, message: "x", next: 105 }, 100.2)).toBe("retry 1 in 5 s");
  expect(statusLabel({ type: "retry", attempt: 3, message: "x", next: 99 }, 100)).toBe("retry 3 in 0 s");
});

test("openTurns and tree", () => {
  const t = (n: number, state: any) => ({ n, state, started: 0, files: {} });
  expect(openTurns(session("s", { turns: [t(1, "accepted"), t(2, "open"), t(3, "empty"), t(4, "open")] }))).toEqual([2, 4]);
  const all = { a: session("a", { created: 1 }), b: session("b", { created: 2 }), c: session("c", { created: 3, parentId: "a" }) };
  expect(tree(all).map((x) => [x.session.id, x.children.map((c) => c.id)])).toEqual([["b", []], ["a", ["c"]]]);
});

test("parseDiff numbers old and new lines", () => {
  const d = parseDiff("--- a/x\n+++ b/x\n@@ -3,2 +3,2 @@\n ctx\n-old\n+new\n");
  expect(d.map((l) => [l.kind, l.old, l.new])).toEqual([
    ["meta", null, null], ["meta", null, null], ["hunk", null, null], ["ctx", 3, 3], ["del", 4, null], ["add", null, 4]]);
});

describe("store actions", () => {
  beforeEach(() => useSessions.setState({ sessions: {}, parts: {}, activity: {}, open: null, pendingRevert: null, error: null }));

  test("a revert conflict waits for skip or overwrite", async () => {
    const conflicts = [{ path: "recipes/km.py", turn: 2, reason: "changed after the agent's edit" }];
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { message: "conflict", conflicts } }), { status: 409 })));
    await useSessions.getState().revert("s1", 2);
    expect(useSessions.getState().pendingRevert).toEqual({ sessionId: "s1", turn: 2, conflicts });
    const body = { session: session("s1"), reverted: [2], restored: [], conflicts };
    const f = vi.fn(async () => new Response(JSON.stringify(body)));
    vi.stubGlobal("fetch", f);
    await useSessions.getState().revert("s1", 2, "skip");
    expect(JSON.parse((f.mock.calls[0] as any)[1].body)).toEqual({ turn: 2, conflicts: "skip" });
    expect(useSessions.getState().pendingRevert).toBeNull();
    vi.unstubAllGlobals();
  });

  test("show merges parts that arrived while loading", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      useSessions.getState().onEvent({ type: "part.added", sessionId: "s1", part: tool("late", "pending", 5) });
      return new Response(JSON.stringify({ session: session("s1"), parts: [tool("p1", "done", 1)] }));
    }));
    await useSessions.getState().show("s1");
    expect(useSessions.getState().parts.s1.map((p) => p.id)).toEqual(["p1", "late"]);
    vi.unstubAllGlobals();
  });
});
