"""Command line: ``pintu serve``, ``pintu agent`` and ``pintu adapt``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _size(text: str) -> tuple[float, float]:
    w, _, h = text.lower().partition("x")
    return float(w), float(h)


def _agent_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--project", default=".", help="project folder, a clean git tree (default: .)")
    ap.add_argument("--board", required=True, help="board name")
    ap.add_argument("--panel", required=True, help="panel id with a recipe source")
    ap.add_argument("--size", type=_size, help="target size WxH in mm (default: the cell size)")
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
    from .recipes import SubprocessRunner

    project = Project.open(args.project)
    try:
        dirty = ag.git_dirty(project.root)
    except Exception:
        print(f"pintu: {project.root} is not a git repository", file=sys.stderr)
        return 2
    if dirty:
        print("pintu: commit or stash changes first; the agent needs a clean tree:\n" + "\n".join(dirty),
              file=sys.stderr)
        return 2
    llm = LLM(load_profile(args.profile, args.llm_config))
    runner = SubprocessRunner(project)
    try:
        a = ag.Agent(project, args.board, args.panel, runner, llm, size=args.size, on_event=_progress,
                     max_steps=args.max_steps)
        if prompt is None:
            recipe, params, _ = a.recipe(args.panel)
            if not args.size:  # the cell changed since the last render
                a.old_size = ag.last_size(project, recipe, params) or a.cell_size
            prompt = ag.adapt_prompt(args.panel, a.old_size, a.size)
        res = await a.run(prompt)
        final, w, h = await a.render(args.panel)
    finally:
        await runner.close()
    print(res.text)
    print(f"\n--- {res.stopped} after {res.steps} steps, {res.usage.get('total', 0)} tokens, {res.seconds:.0f} s")
    print(f"transcript: {res.transcript}")
    print(f"final render ({w:g} x {h:g} mm): " + (str(project.root / final.svg) if final.ok else "FAILED"))
    print(ag.git(project.root, "diff") or "(no changes)")
    print("revert with: git checkout -- .")
    return 0 if final.ok else 1


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``pintu`` command."""
    ap = argparse.ArgumentParser(prog="pintu", description="Storyboard app for academic figures.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="serve a project to the browser")
    s.add_argument("--project", default=".", help="project folder (default: .)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--dev", action="store_true", help="API only; the Vite dev server serves the frontend")
    a = sub.add_parser("agent", help="prompt the LLM agent about a panel")
    _agent_args(a)
    a.add_argument("prompt")
    d = sub.add_parser("adapt", help="let the agent adapt a panel's recipe to a new size")
    _agent_args(d)
    args = ap.parse_args(argv)
    if args.cmd in ("agent", "adapt"):
        logging.basicConfig(level=logging.WARNING)
        sys.exit(asyncio.run(_run_agent(args, getattr(args, "prompt", None))))

    import uvicorn

    from .project import Project
    from .server import create_app

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    project = Project.open(args.project, create=True)
    app = create_app(project, dev=args.dev)
    where = "API for the Vite dev server" if args.dev else "open"
    print(f"pintu: {project.root} -> {where} http://{args.host}:{args.port}/", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info" if args.dev else "warning")
