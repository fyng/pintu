"""Command line: ``pintu serve``, ``agent``, ``adapt``, ``accept``, ``revert``, ``mcp``,
``login copilot``, ``models copilot`` and ``stylepack check``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys


def _size(text: str) -> tuple[float, float]:
    w, _, h = text.lower().partition("x")
    return float(w), float(h)


def _agent_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--project", default=".", help="project folder (default: .)")
    ap.add_argument("--board", required=True, help="board name")
    ap.add_argument("--panel", required=True, help="panel id with a recipe source")
    ap.add_argument("--size", type=_size, help="target size WxH in mm (default: the cell size below the letter band)")
    ap.add_argument("--profile", help="profile in llm.toml (default: $PINTU_LLM_PROFILE or the first)")
    ap.add_argument("--llm-config", help="llm.toml path (default: $PINTU_LLM_CONFIG or ~/.config/pintu/llm.toml)")
    ap.add_argument("--max-steps", type=int, default=20)


def _progress(rec: dict) -> None:
    if rec["type"] == "reply":
        calls = ", ".join(c["name"] for c in rec["tool_calls"]) or "(done)"
        print(f"step {rec['step']}: {rec['seconds']:.0f} s, {rec['usage'].get('total', '?')} tokens -> {calls}",
              file=sys.stderr, flush=True)


async def _run_agent(args, prompt: str | None) -> int:
    from . import agent as ag
    from .llm import LLM, load_profile
    from .project import Project
    from .sessions import SessionError, SessionManager

    project = Project.open(args.project)
    llm = LLM(load_profile(args.profile, args.llm_config))
    renders = ag.local_renders(project)
    mgr = SessionManager(project, renders, lambda: llm, on_record=_progress, max_steps=args.max_steps)
    try:
        try:
            s = await mgr.create("adapt" if prompt is None else "prompt", args.board, args.panel, prompt,
                                 size=list(args.size) if args.size else None)
        except SessionError as e:
            print(f"pintu: {e}", file=sys.stderr)
            return 2
        a = mgr.agents[s["id"]]
        s = await mgr.wait(s["id"])
        final, w, h = await a.render(args.panel)
    finally:
        await renders.stop()
    res = s["result"] or {}
    print(res.get("text") or s.get("error") or "")
    print(f"\n--- {res.get('stopped')} after {s['steps']} steps, {s['usage'].get('total', 0)} tokens, "
          f"{res.get('seconds', 0):.0f} s")
    print(f"session: {s['id']}; transcript: {project.root / s['transcript']}")
    print(f"final render ({w:g} x {h:g} mm): " + (str(project.root / final.svg) if final.ok else "FAILED"))
    print(mgr.diff(s["id"])["diff"] or "(no changes)")
    print(f"accept with: pintu accept {s['id']}; revert with: pintu revert {s['id']}")
    return 0 if final.ok else 1


def _undo(args) -> int:
    from .project import Project
    from .sessions import SessionError, SessionManager

    mgr = SessionManager(Project.open(args.project), None, lambda: None)
    try:
        if args.cmd == "accept":
            out = mgr.accept(args.session, args.turn)
            print(f"accepted turns {out['accepted'] or 'none'}")
        else:
            out = mgr.revert(args.session, args.turn, "overwrite" if args.force else "fail")
            print(f"reverted turns {out['reverted'] or 'none'}: " + (", ".join(out["restored"]) or "no files"))
    except SessionError as e:
        print(f"pintu: {e}", file=sys.stderr)
        return 1
    return 0


def _copilot(args) -> int:
    from . import copilot
    from .llm import load_profile

    try:
        if args.cmd == "login":
            client_id = args.client_id or os.environ.get(copilot.CLIENT_ID_ENV)
            if not client_id:
                print(f"pintu: pass --client-id or set ${copilot.CLIENT_ID_ENV} (see docs/dev.md)", file=sys.stderr)
                return 2
            path = copilot.login(client_id, out=lambda s: print(s, flush=True))
            print(f"logged in; GitHub token saved to {path}")
            return 0
        prof = load_profile(args.profile, args.llm_config) if args.profile else None
        auth = copilot.CopilotAuth(exchange=bool(prof and prof.token_exchange))
        models = copilot.list_models(auth, prof.base_url if prof else copilot.API)
    except Exception as e:
        print(f"pintu: {copilot.redact(str(e))}", file=sys.stderr)
        return 1
    print(f"{'model':<32} {'tools':<6} {'vision':<7} {'picker':<7} endpoints")
    for m in sorted(models, key=lambda m: m["id"]):
        print(f"{m['id']:<32} {str(m['tools']).lower():<6} {str(m['vision']).lower():<7} "
              f"{str(m['picker']).lower():<7} {' '.join(m['endpoints']) or '-'}")
    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``pintu`` command."""
    ap = argparse.ArgumentParser(prog="pintu", description="Storyboard app for academic figures.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="serve a project to the browser")
    s.add_argument("--project", default=".", help="project folder (default: .)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--dev", action="store_true", help="API only; the Vite dev server serves the frontend")
    s.add_argument("--profile", help="LLM profile for agent sessions (default: $PINTU_LLM_PROFILE or the first)")
    s.add_argument("--llm-config", help="llm.toml path (default: $PINTU_LLM_CONFIG or ~/.config/pintu/llm.toml)")
    a = sub.add_parser("agent", help="prompt the LLM agent about a panel")
    _agent_args(a)
    a.add_argument("prompt")
    d = sub.add_parser("adapt", help="let the agent adapt a panel's recipe to a new size")
    _agent_args(d)
    for name, what in (("accept", "keep"), ("revert", "undo")):
        u = sub.add_parser(name, help=f"{what} an agent session's changes")
        u.add_argument("session", help="session id")
        u.add_argument("--project", default=".", help="project folder (default: .)")
        u.add_argument("--turn", type=int, help="accept up to / revert from this turn (default: all open turns)")
        if name == "revert":
            u.add_argument("--force", action="store_true", help="also restore files changed after the agent's edit")
    mc = sub.add_parser("mcp", help="serve the MCP tools over stdio, for terminal agents")
    mc.add_argument("--project", default=".", help="project folder (default: .)")
    lg = sub.add_parser("login", help="log in to a hosted provider (GitHub device flow)")
    lg.add_argument("provider", choices=["copilot"])
    lg.add_argument("--client-id", help="GitHub OAuth app client id (default: $PINTU_COPILOT_CLIENT_ID)")
    m = sub.add_parser("models", help="list a hosted provider's models")
    m.add_argument("provider", choices=["copilot"])
    m.add_argument("--profile", help="copilot profile in llm.toml, for its base_url and token_exchange")
    m.add_argument("--llm-config", help="llm.toml path")
    sp = sub.add_parser("stylepack", help="validate a style pack and print its rules")
    sp.add_argument("action", choices=["check"])
    sp.add_argument("path", help="pack folder or its stylepack.toml")
    args = ap.parse_args(argv)
    if args.cmd == "stylepack":
        from . import style
        sys.exit(style.check(args.path))
    if args.cmd in ("login", "models"):
        sys.exit(_copilot(args))
    if args.cmd in ("agent", "adapt"):
        logging.basicConfig(level=logging.WARNING)
        sys.exit(asyncio.run(_run_agent(args, getattr(args, "prompt", None))))
    if args.cmd in ("accept", "revert"):
        sys.exit(_undo(args))
    if args.cmd == "mcp":
        from .mcp_server import run_stdio
        from .project import Project

        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        run_stdio(Project.open(args.project))
        return

    import uvicorn

    from .project import Project
    from .server import create_app, default_llm

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    project = Project.open(args.project, create=True)
    app = create_app(project, dev=args.dev, llm_factory=default_llm(args.profile, args.llm_config))
    where = "API for the Vite dev server" if args.dev else "open"
    print(f"pintu: {project.root} -> {where} http://{args.host}:{args.port}/", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info" if args.dev else "warning")
