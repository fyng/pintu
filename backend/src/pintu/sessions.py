"""Agent sessions: typed message parts, status, abort, child sessions, checkpoints (SPEC §9).

A session is one agent conversation, optionally bound to a board panel. Each prompt
is a *turn*; a turn starts with a checkpoint (``checkpoints.py``) and ends with a
``patch`` part holding the diff of the files the agent changed. Turns stay ``open``
until accepted or reverted. The API shape follows opencode's server (sessions, parts,
a status map); ``docs/api-sessions.md`` is the contract.

Sessions persist as ``.pintu/sessions/<id>.json`` (info, parts and the model history),
so a restart keeps them; a session that was busy comes back idle, its running tool
parts marked as errors.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from . import agent as ag, fit as fits, style as styles
from .board import Board, BoardError
from .catalog import read_sidecar, scripts_for
from .checkpoints import Checkpoints, Conflict
from .project import PathError, Project
from .recipes import locate, size_range, outside
from .renders import Renders

log = logging.getLogger("pintu")
DIR = ".pintu/sessions"
KINDS = ("prompt", "adapt", "promote")
MAX_OUTPUT = 20_000
RETRY_DELAYS = (2.0, 5.0, 10.0)
_RECIPE_LINE = re.compile(r"RECIPE:\s*`?([\w.]+:[\w.]+)`?")


class SessionError(Exception):
    """A request the session manager refuses.

    Attributes:
        status: HTTP status for the API (400, 404 or 409).
    """

    def __init__(self, message: str, status: int = 400, detail: Any = None):
        super().__init__(message)
        self.status, self.detail = status, detail if detail is not None else message


def _id(prefix: str) -> str:
    """Time-sortable id: ``<prefix>_<ms hex><random hex>``."""
    return f"{prefix}_{int(time.time() * 1000):011x}{secrets.token_hex(4)}"


def _now() -> float:
    return round(time.time(), 3)


def _transient(e: BaseException) -> bool:
    """Whether an LLM error is worth a retry: connection, timeout, 408/409/429 or 5xx."""
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        return code in (408, 409, 429) or code >= 500
    return type(e).__name__ in ("APIConnectionError", "APITimeoutError") or isinstance(e, (ConnectionError, TimeoutError))


def _plain(messages: list[dict]) -> list[dict]:
    """The model history with images replaced by text, for the session file."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            m = {**m, "content": [{"type": "text", "text": "[render image not kept]"} if p.get("type") == "image_url"
                                  else p for p in m["content"]]}
        out.append(m)
    return out


class _Retrying:
    """An LLM wrapper that retries transient errors and reports the retry status."""

    def __init__(self, llm: Any, on_retry: Callable[[Optional[dict]], None], delays: tuple = RETRY_DELAYS):
        self.llm, self.profile, self.on_retry, self.delays = llm, llm.profile, on_retry, delays

    async def chat(self, messages: list[dict], tools: list[dict]):
        for attempt in range(len(self.delays) + 1):
            try:
                reply = await self.llm.chat(messages, tools)
            except Exception as e:
                if attempt == len(self.delays) or not _transient(e):
                    raise
                delay = self.delays[attempt]
                self.on_retry({"type": "retry", "attempt": attempt + 1, "message": f"{type(e).__name__}: {e}"[:500],
                               "next": _now() + delay})
                await asyncio.sleep(delay)
                continue
            if attempt:
                self.on_retry(None)
            return reply


class _Guard:
    """Routes an agent's file writes to its session's current turn checkpoint."""

    def __init__(self, mgr: "SessionManager", s: dict):
        self.mgr, self.s = mgr, s

    def before(self, rel: str) -> None:
        self.mgr.ck.before(self.s["turns"][-1], rel)

    def after(self, rel: str) -> None:
        self.mgr.ck.after(self.s["turns"][-1], rel)
        self.mgr._changed([rel])


class SessionManager:
    """Agent sessions of one project.

    Args:
        project: The project.
        renders: Render path for the agents (the board's ``State.renders`` in the server).
        llm_factory: Returns the model client for a new session; raises if no profile is set up.
        notify: Awaited with each WebSocket event (``session.*``, ``part.*``).
        on_files: Called with root-relative paths the agent or a revert changed.
        on_record: Also called with each agent transcript record, e.g. for CLI progress.
        checkpoints: Checkpoint store; default: git if the project is in a work tree, else copies.
        max_steps: Cap on model calls per turn.
    """

    def __init__(self, project: Project, renders: Optional[Renders], llm_factory: Callable[[], Any],
                 notify: Optional[Callable[[dict], Awaitable[None]]] = None,
                 on_files: Optional[Callable[[list[str]], None]] = None,
                 on_record: Optional[Callable[[dict], None]] = None,
                 checkpoints: Optional[Checkpoints] = None, max_steps: int = ag.MAX_STEPS):
        self.project, self.renders, self.llm_factory = project, renders, llm_factory
        self.notify, self.on_files, self.on_record, self.max_steps = notify, on_files, on_record, max_steps
        self.ck = checkpoints or Checkpoints(project)
        self.pack = styles.for_project(project)
        self.retry_delays = RETRY_DELAYS
        self.dir = project.root / DIR
        self.sessions: dict[str, dict] = {}
        self.parts: dict[str, list[dict]] = {}
        self.history: dict[str, list[dict]] = {}
        self.agents: dict[str, ag.Agent] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._calls: dict[tuple, dict] = {}
        self._queue: Optional[asyncio.Queue] = None
        self._sender: Optional[asyncio.Task] = None
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self.dir.is_dir():
            return
        for f in sorted(self.dir.glob("ses_*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                s = data["session"]
            except (OSError, ValueError, KeyError) as e:
                log.warning("skipping session file %s: %s", f, e)
                continue
            self.sessions[s["id"]] = s
            self.parts[s["id"]] = data.get("parts", [])
            self.history[s["id"]] = data.get("messages", [])
            if s["status"]["type"] != "idle":  # the server stopped mid-turn
                for p in self.parts[s["id"]]:
                    if p["type"] == "tool" and p["state"]["status"] in ("pending", "running"):
                        p["state"] = {**p["state"], "status": "error", "error": "interrupted: the server stopped"}
                t = s["turns"][-1] if s["turns"] else None
                if t and t["state"] == "running":
                    self._close_turn(s, t, "interrupted")
                s["status"] = {"type": "idle"}
                self._save(s)

    def _save(self, s: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / f"{s['id']}.json"
        tmp = f.with_name(f.name + f".{os.getpid()}.tmp")
        hist = _plain(self.agents[s["id"]].messages) if s["id"] in self.agents else self.history.get(s["id"], [])
        tmp.write_text(json.dumps({"session": s, "parts": self.parts[s["id"]], "messages": hist}, default=str),
                       encoding="utf-8")
        tmp.replace(f)

    # -- events -------------------------------------------------------------

    def _emit(self, msg: dict) -> None:
        if self.notify is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._queue is None or self._sender is None or self._sender.done():
            self._queue = asyncio.Queue()
            self._sender = loop.create_task(self._send())
        self._queue.put_nowait(msg)

    async def _send(self) -> None:
        while True:
            msg = await self._queue.get()
            try:
                await self.notify(msg)
            except Exception:
                log.exception("session event failed")

    async def flush(self) -> None:
        """Waits until queued events are sent."""
        while self._queue is not None and not self._queue.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)

    def _touch(self, s: dict, save: bool = True) -> None:
        s["updated"] = _now()
        if save:
            self._save(s)
        self._emit({"type": "session.updated", "session": s})

    def _set_status(self, s: dict, status: Optional[dict]) -> None:
        s["status"] = status or {"type": "busy"}
        self._save(s)
        self._emit({"type": "session.status", "sessionId": s["id"], "status": s["status"]})

    def _add(self, s: dict, part: dict) -> dict:
        part = {"id": _id("prt"), "sessionId": s["id"], "turn": len(s["turns"]), "time": {"start": _now()}, **part}
        self.parts[s["id"]].append(part)
        self._emit({"type": "part.added", "sessionId": s["id"], "part": part})
        return part

    def _update(self, s: dict, part: dict, save: bool = False) -> None:
        if save:
            self._save(s)
        self._emit({"type": "part.updated", "sessionId": s["id"], "part": part})

    def _changed(self, paths: list[str]) -> None:
        if self.on_files and paths:
            try:
                self.on_files(paths)
            except Exception:
                log.exception("on_files failed")

    def _on_event(self, s: dict, rec: dict) -> None:
        """Maps agent transcript records to parts."""
        if self.on_record:
            self.on_record(rec)
        kind = rec["type"]
        if kind == "reply":
            if rec.get("reasoning"):
                p = self._add(s, {"type": "reasoning", "text": rec["reasoning"], "step": rec["step"]})
                p["time"]["end"] = p["time"]["start"]
            if rec.get("content"):
                p = self._add(s, {"type": "text", "role": "assistant", "text": rec["content"], "step": rec["step"]})
                p["time"]["end"] = p["time"]["start"]
            for c in rec["tool_calls"]:
                p = self._add(s, {"type": "tool", "callId": c["id"], "tool": c["name"], "input": c["arguments"],
                                  "step": rec["step"], "state": {"status": "pending"}})
                self._calls[(s["id"], rec["step"], c["id"])] = p
            s.setdefault("usage", {})
            for k, v in (rec.get("usage") or {}).items():
                s["usage"][k] = s["usage"].get(k, 0) + (v or 0)
            s["steps"] = s.get("steps", 0) + 1
            self._touch(s)
        elif kind == "tool_start":
            p = self._calls.get((s["id"], rec["step"], rec["id"]))
            if p:
                p["state"] = {"status": "running", "time": {"start": _now()}}
                self._update(s, p)
        elif kind == "tool":
            p = self._calls.pop((s["id"], rec["step"], rec["id"]), None)
            if p:
                out = str(rec["result"])
                st = {"status": "error" if out.startswith("error:") else "done",
                      "time": {"start": p["state"].get("time", {}).get("start", _now()), "end": _now()},
                      "output": out[:MAX_OUTPUT] + (f"\n[... {len(out) - MAX_OUTPUT} more chars]"
                                                    if len(out) > MAX_OUTPUT else ""),
                      "image": bool(rec.get("image"))}
                if st["status"] == "error":
                    st["error"] = out[len("error:"):].strip()[:2000]
                p["state"] = st
                p["time"]["end"] = _now()
                self._update(s, p, save=True)

    # -- queries ------------------------------------------------------------

    def get_session(self, sid: str) -> dict:
        if sid not in self.sessions:
            raise SessionError(f"no session {sid!r}", 404)
        return self.sessions[sid]

    def list(self, board: Optional[str] = None, panel: Optional[str] = None) -> list[dict]:
        """Sessions, newest first, optionally of one board and panel."""
        out = [s for s in self.sessions.values()
               if (board is None or s["board"] == board) and (panel is None or s["panel"] == panel)]
        return sorted(out, key=lambda s: s["created"], reverse=True)

    def status(self) -> dict[str, dict]:
        """Session id → status, for sessions that are not idle."""
        return {sid: s["status"] for sid, s in self.sessions.items() if s["status"]["type"] != "idle"}

    def get(self, sid: str) -> dict:
        """The session and its parts."""
        return {"session": self.get_session(sid), "parts": self.parts[sid]}

    def diff(self, sid: str, turn: Optional[int] = None) -> dict:
        """Diff of one turn, or of the session's open turns."""
        s = self.get_session(sid)
        turns = [t for t in s["turns"] if t["n"] == turn] if turn else [t for t in s["turns"] if t["state"] == "open"]
        if turn and not turns:
            raise SessionError(f"session {sid} has no turn {turn}", 404)
        files = self.ck.diff(turns)
        return {"sessionId": sid, "turn": turn, "files": files, "diff": "".join(f["diff"] for f in files)}

    def busy_for(self, board: Optional[str], panel: Optional[str]) -> Optional[str]:
        """The busy top-level session bound to a panel, if any."""
        if not panel:
            return None
        return next((sid for sid, s in self.sessions.items() if s["board"] == board and s["panel"] == panel
                     and s["parentId"] is None and s["status"]["type"] != "idle"), None)

    # -- entry points -------------------------------------------------------

    async def create(self, kind: str = "prompt", board: Optional[str] = None, panel: Optional[str] = None,
                     prompt: Optional[str] = None, size: Optional[list] = None, old_size: Optional[list] = None,
                     path: Optional[str] = None, parent_id: Optional[str] = None, title: Optional[str] = None) -> dict:
        """Creates a session and starts its first turn, if it has a prompt.

        Args:
            kind: ``prompt`` (free text), ``adapt`` (templated; needs a recipe panel) or
                ``promote`` (templated; needs a gallery ``path`` whose script is known).
            board: Board name.
            panel: Panel id the session is bound to.
            prompt: The first prompt; ``adapt`` and ``promote`` build their own (``prompt`` is appended).
            size: Target (w, h) in mm for ``adapt``; default: the panel's size below its letter band.
            old_size: Size the recipe was designed for; default: its last render.
            path: Gallery item for ``promote``.
            parent_id: Makes a child session (a subtask) of this one.
            title: Display title.

        Raises:
            SessionError: Bad arguments (400), unknown parent (404) or a busy session on the panel (409).
        """
        if kind not in KINDS:
            raise SessionError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
        if parent_id is not None:
            parent = self.get_session(parent_id)
            board = board if board is not None else parent["board"]
        if board is not None:
            try:
                b = Board.load(self.project.board_path(board), self.pack)
                if panel is not None:
                    b.panel(panel)
            except (BoardError, PathError, FileNotFoundError, KeyError) as e:
                raise SessionError(f"bad board or panel: {e}", 404 if isinstance(e, (FileNotFoundError, KeyError)) else 400)
        elif panel is not None:
            raise SessionError("a panel needs a board")
        if parent_id is None and (busy := self.busy_for(board, panel)):
            raise SessionError(f"panel {panel!r} has a busy session {busy}", 409, {"message": "busy", "sessionId": busy})
        promote = self._promote_source(path) if kind == "promote" else None
        text, extra, size, old_size = self._template(kind, board, panel, prompt, size, old_size, promote)
        s = {"id": _id("ses"), "parentId": parent_id, "kind": kind, "title": title or (text or kind)[:80].split("\n")[0],
             "board": board, "panel": panel, "created": _now(), "updated": _now(), "status": {"type": "idle"},
             "turns": [], "size": size, "oldSize": old_size, "extra": extra, "promote": promote,
             "profile": None, "error": None, "result": None, "usage": {}, "steps": 0}
        if text:
            self._make_agent(s)  # fails before the session exists, e.g. without an LLM profile
        self.sessions[s["id"]] = s
        self.parts[s["id"]] = []
        self._save(s)
        self._emit({"type": "session.created", "session": s})
        if text:
            await self._start(s, text)
        return s

    def _promote_source(self, path: Optional[str]) -> dict:
        """The gallery item's script: the sidecar's ``script``, else its ``recipe``'s module file."""
        if not path:
            raise SessionError("promote needs the gallery item's path")
        return promote_source(self.project, path)

    def _template(self, kind, board, panel, prompt, size, old_size, promote):
        extra: list[str] = []
        if kind == "prompt":
            return prompt, extra, size, old_size
        if kind == "promote":
            text = ag.PROMOTE.format(**promote["fields"])
            return text + (f"\n\nAlso: {prompt}" if prompt else ""), extra, size, old_size
        if not board or not panel:
            raise SessionError("adapt needs a board and a recipe panel")
        b = Board.load(self.project.board_path(board), self.pack)
        src = b.panel(panel).get("source") or {}
        if "recipe" not in src:
            raise SessionError(f"panel {panel!r} has no recipe source")
        _, _, w, h = b.content_rect(b.panel(panel))
        size = list(size) if size else [round(w, 3), round(h, 3)]
        old_size = list(old_size) if old_size else list(
            ag.last_size(self.project, str(src["recipe"]), dict(src.get("params") or {})) or size)
        text = ag.adapt_prompt(panel, tuple(old_size), tuple(size))
        rng = size_range(self.project, str(src["recipe"]))
        if rng:
            text += (f"\n\nThe recipe's @panel declares min_size={rng['min_size']} and max_size={rng['max_size']}"
                     + ("; the new size is outside it. When the recipe works at the new size, widen the range"
                        " in the decorator to cover it." if outside(rng, *size) else "."))
        return text + (f"\n\nAlso: {prompt}" if prompt else ""), extra, size, old_size

    async def prompt(self, sid: str, text: str, size: Optional[list] = None) -> dict:
        """Starts a new turn in an idle session.

        Raises:
            SessionError: Unknown session (404) or busy (409).
        """
        s = self.get_session(sid)
        if s["status"]["type"] != "idle":
            raise SessionError(f"session {sid} is busy", 409)
        if not text or not text.strip():
            raise SessionError("empty prompt")
        if size:
            s["size"] = list(size)
            if sid in self.agents:
                self.agents[sid].size = tuple(size)
        await self._start(s, text)
        return s

    def _agent(self, s: dict) -> ag.Agent:
        if s["id"] in self.agents:
            return self.agents[s["id"]]
        llm = self.llm_factory()
        s["profile"] = {"name": llm.profile.name, "model": llm.profile.model, "tools": llm.profile.tools,
                        "vision": llm.profile.vision}
        wrapped = _Retrying(llm, lambda st: self._set_status(s, st), self.retry_delays)
        panel = None if s["kind"] == "promote" else s["panel"]
        steps = self.max_steps
        if panel:
            src = Board.load(self.project.board_path(s["board"]), self.pack).panel(panel).get("source") or {}
            if "recipe" not in src:
                steps += ag.RECIPE_STEPS
        a = ag.Agent(self.project, s["board"], panel, self.renders, wrapped,
                     size=tuple(s["size"]) if s.get("size") and panel else None,
                     old_size=tuple(s["oldSize"]) if s.get("oldSize") and panel else None,
                     max_steps=steps, on_event=lambda rec: self._on_event(s, rec),
                     tools=ag.PROMOTE_TOOLS if s["kind"] == "promote" else None,
                     guard=_Guard(self, s), extra=list(s.get("extra") or []))
        a.messages = list(self.history.get(s["id"], []))
        s["transcript"] = self.project.relative(a.transcript)
        self.agents[s["id"]] = a
        return a

    def _make_agent(self, s: dict) -> ag.Agent:
        try:
            return self._agent(s)
        except SessionError:
            raise
        except Exception as e:
            raise SessionError(f"cannot start the agent: {e}")

    async def _start(self, s: dict, text: str) -> None:
        a = self._make_agent(s)
        n = len(s["turns"]) + 1
        ck = await asyncio.to_thread(self.ck.begin, s["id"], n)
        s["turns"].append({"n": n, "state": "running", "checkpoint": ck, "files": {}, "started": _now()})
        p = self._add(s, {"type": "text", "role": "user", "text": text})
        p["time"]["end"] = p["time"]["start"]
        s["error"] = None
        self._set_status(s, {"type": "busy"})
        self.tasks[s["id"]] = asyncio.create_task(self._run(s, a, text))

    async def _run(self, s: dict, a: ag.Agent, text: str) -> None:
        t = s["turns"][-1]
        outcome = "done"
        try:
            res = await a.run(text)
            outcome = res.stopped
            s["result"] = {"stopped": res.stopped, "steps": res.steps, "seconds": round(res.seconds, 2),
                           "text": res.text}
            if s["kind"] == "promote":
                s["result"]["recipe"] = self._promoted(s, res.text)
        except asyncio.CancelledError:
            outcome = "aborted"
            s["result"] = {"stopped": "aborted"}
        except Exception as e:
            outcome = "error"
            log.exception("session %s failed", s["id"])
            s["error"] = f"{type(e).__name__}: {e}"[:2000]
            s["result"] = {"stopped": "error"}
        finally:
            self._close_turn(s, t, outcome)
            s["status"] = {"type": "idle"}
            self._save(s)
            self._emit({"type": "session.status", "sessionId": s["id"], "status": s["status"]})
            self._touch(s, save=False)
            self.tasks.pop(s["id"], None)

    def _close_turn(self, s: dict, t: dict, outcome: str) -> None:
        """Ends a turn: fails its unfinished tool parts, keeps its blobs, adds its patch part."""
        for p in self.parts[s["id"]]:
            if p["type"] == "tool" and p["turn"] == t["n"] and p["state"]["status"] in ("pending", "running"):
                p["state"] = {**p["state"], "status": "error", "error": outcome}
                p["time"]["end"] = _now()
                self._update(s, p)
        for k in [k for k in self._calls if k[0] == s["id"]]:
            del self._calls[k]
        try:
            self.ck.finish(t)
            files = self.ck.diff([t])
        except Exception as e:
            log.exception("checkpoint of session %s turn %s", s["id"], t["n"])
            files, s["error"] = [], f"checkpoint: {e}"
        t["outcome"], t["ended"] = outcome, _now()
        if files:
            t["state"] = "open"
            p = self._add(s, {"type": "patch", "files": files, "outcome": outcome})
            p["turn"] = t["n"]
            p["time"]["end"] = p["time"]["start"]
        else:
            t["state"] = "empty"
            self.ck.drop(t)
        self._changed(list(t.get("files") or {}))

    def _promoted(self, s: dict, text: str) -> Optional[str]:
        """The recipe a promote session wrote: its ``RECIPE:`` line, else the suggested name, if it exists."""
        m = _RECIPE_LINE.search(text or "")
        for r in ([m.group(1)] if m else []) + [s["promote"]["fields"]["recipe"]]:
            if locate(self.project, r):
                return r
        return None

    # -- control ------------------------------------------------------------

    async def abort(self, sid: str) -> dict:
        """Cancels the running LLM call or tool, and aborts the session's children."""
        s = self.get_session(sid)
        for c in [c for c in self.sessions.values() if c["parentId"] == sid]:
            await self.abort(c["id"])
        task = self.tasks.get(sid)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return s

    async def wait(self, sid: str) -> dict:
        """Waits for the session's running turn to end."""
        task = self.tasks.get(sid)
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)
        return self.get_session(sid)

    def _idle(self, sid: str) -> dict:
        s = self.get_session(sid)
        if s["status"]["type"] != "idle":
            raise SessionError(f"session {sid} is busy", 409)
        return s

    def accept(self, sid: str, turn: Optional[int] = None) -> dict:
        """Keeps the changes of the open turns up to ``turn`` (default: all) and drops their checkpoints."""
        s = self._idle(sid)
        done = []
        for t in s["turns"]:
            if t["state"] == "open" and (turn is None or t["n"] <= turn):
                self.ck.drop(t)
                t["state"] = "accepted"
                done.append(t["n"])
        self._touch(s)
        return {"session": s, "accepted": done}

    def revert(self, sid: str, turn: Optional[int] = None, conflicts: str = "fail") -> dict:
        """Restores the files of the open turns from ``turn`` on (default: all), newest first.

        Only files the agent wrote are touched. A file changed since the agent's last
        write to it is a conflict: ``fail`` changes nothing (409), ``skip`` keeps it,
        ``overwrite`` restores it anyway.
        """
        s = self._idle(sid)
        if conflicts not in ("fail", "skip", "overwrite"):
            raise SessionError("conflicts must be fail, skip or overwrite")
        turns = [t for t in s["turns"] if t["state"] == "open" and (turn is None or t["n"] >= turn)]
        try:
            out = self.ck.revert(turns, conflicts)
        except Conflict as e:
            raise SessionError(str(e), 409, {"message": "conflict", "conflicts": e.conflicts})
        for t in turns:
            self.ck.drop(t)
            t["state"] = "reverted"
            kept = [c["path"] for c in out["conflicts"] if c["turn"] == t["n"]] if conflicts == "skip" else []
            if kept:
                t["kept"] = kept
        self._touch(s)
        self._changed(out["restored"])
        return {"session": s, "reverted": [t["n"] for t in turns], **out}

    # -- size range ---------------------------------------------------------

    async def on_resize(self, board: str, before: Board, after: Board) -> list[str]:
        """Starts an "Adapt to size" session for each recipe panel a board edit resized
        outside its ``@panel`` size range. Returns the new session ids."""
        started = []
        for p in after.panels:
            src = p.get("source") or {}
            if "recipe" not in src:
                continue
            try:
                old = before.panel(p["id"])
            except KeyError:
                continue
            _, _, w, h = after.content_rect(p)
            _, _, w0, h0 = before.content_rect(old)
            if (round(w, 2), round(h, 2)) == (round(w0, 2), round(h0, 2)):
                continue
            rng = size_range(self.project, str(src["recipe"]))
            if not outside(rng, w, h) or self.busy_for(board, p["id"]):
                continue
            try:
                s = await self.create("adapt", board, p["id"], size=[round(w, 3), round(h, 3)],
                                      old_size=[round(w0, 3), round(h0, 3)], title=f"Adapt {p['id']} to size")
            except SessionError as e:
                log.warning("adapt to size for %s: %s", p["id"], e)
                continue
            started.append(s["id"])
        return started

    async def close(self) -> None:
        """Aborts running turns and waits for queued events."""
        for sid in list(self.tasks):
            await self.abort(sid)
        await self.flush()
        if self._sender:
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender


def promote_source(project: Project, path: str) -> dict:
    """What "Promote to recipe" knows of a gallery item.

    The source script is the first of ``catalog.scripts_for``: the sidecar's ``script``
    (written by ``pintu_sdk.save``), the module file of its ``recipe``, else a project
    ``.py`` file that names the file. Returns ``path``, ``script``, ``params``, ``size``
    and the prompt ``fields`` (suggested module and recipe name).

    Raises:
        SessionError: No such file (404), or its script is not known (400).
    """
    try:
        p = project.resolve(path)
    except PathError as e:
        raise SessionError(str(e))
    if not p.is_file():
        raise SessionError(f"no file {path!r}", 404)
    meta = read_sidecar(p) or {}
    scripts = scripts_for(project, project.relative(p))
    if not scripts:
        raise SessionError(f"the script that drew {path} is not known: no sidecar names it, and no project "
                           ".py file names the file")
    script = scripts[0]
    params = dict(meta.get("params") or {})
    natural = fits.natural_size(project, project.relative(p)) or (89.0, 55.0)
    size = [float(meta.get("width_mm") or natural[0]), float(meta.get("height_mm") or natural[1])]
    name = ag.new_recipe(project, path)
    fields = {"script": script, "path": path, "size": ag.fmt_size(*size), **name,
              "params": f" with params {json.dumps(params)}" if params else "",
              "sig": "".join(f", {k}={v!r}" for k, v in params.items())}
    return {"path": path, "script": script, "recipe": meta.get("recipe"), "params": params, "size": size,
            "fields": fields}
