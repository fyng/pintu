"""Command line: ``pintu serve --project <dir>``."""

from __future__ import annotations

import argparse
import logging


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``pintu`` command."""
    ap = argparse.ArgumentParser(prog="pintu", description="Storyboard app for academic figures.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="serve a project to the browser")
    s.add_argument("--project", default=".", help="project folder (default: .)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--dev", action="store_true", help="API only; the Vite dev server serves the frontend")
    args = ap.parse_args(argv)

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
