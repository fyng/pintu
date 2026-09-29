"""Gallery API and live catalog updates (SPEC §7)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from watchfiles import Change, DefaultFilter, awatch

from .catalog import SIDECAR, Catalog
from .project import IMAGE_KINDS


class GalleryFilter(DefaultFilter):
    """Image and PDF files, sidecars and new folders under the scan paths."""

    def __init__(self, roots: list[Path], **kw):
        super().__init__(**kw)
        self.roots = roots

    def __call__(self, change, path: str) -> bool:
        p = Path(path)
        # A new folder counts too: files written into it at once can precede its watch.
        wanted = p.suffix.lower() in IMAGE_KINDS or p.name.endswith(SIDECAR) or (change == Change.added and p.is_dir())
        return super().__call__(change, path) and wanted and any(r == p or r in p.parents for r in self.roots)


def router(catalog: Catalog) -> APIRouter:
    """Routes under ``/api/gallery``."""
    r = APIRouter(prefix="/api/gallery")

    def ready() -> None:
        if not catalog.scanned:
            catalog.scan()

    @r.get("")
    def list_items(recipe: Optional[str] = None, q: str = "", filters: str = "{}",
                   offset: int = 0, limit: int = 60):
        """Items as ``Catalog.query``; ``filters`` is a JSON object of key → list of values."""
        try:
            f = json.loads(filters)
            if not isinstance(f, dict):
                raise ValueError
            f = {str(k): [str(v) for v in vs] for k, vs in f.items()}
        except (ValueError, TypeError):
            raise HTTPException(400, "filters must be a JSON object of lists")
        ready()
        return catalog.query(recipe, q, f, max(0, offset), max(1, min(limit, 500)))

    @r.get("/facets")
    def facets(recipe: str):
        ready()
        return {"recipe": recipe, "params": catalog.facets(recipe)}

    @r.get("/thumb")
    def thumb(path: str, v: Optional[int] = None):
        ready()
        try:
            out = catalog.thumb(path)
        except KeyError:
            raise HTTPException(404, "not in the gallery")
        except Exception as e:
            raise HTTPException(422, f"cannot render {path}: {e}")
        return FileResponse(out, media_type="image/png",
                            headers={"Cache-Control": "max-age=31536000, immutable" if v else "no-cache"})

    return r


async def watch(catalog: Catalog, notify: Callable[[dict], Awaitable[None]]) -> None:
    """Scans once, then rescans on file changes under the scan paths and sends ``{"type": "gallery"}``.

    Requests made during the first scan wait for it, so it sends nothing.
    """
    await asyncio.to_thread(catalog.scan)
    roots = catalog.scan_paths()
    if not roots:
        return
    # Watches the whole root, so scan folders created later are seen.
    ignore = [catalog.project.root / ".pintu", catalog.project.build_dir]
    async for _ in awatch(catalog.project.root, watch_filter=GalleryFilter(roots, ignore_paths=ignore)):
        if await asyncio.to_thread(catalog.scan):
            await notify({"type": "gallery"})
