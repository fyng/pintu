"""Renders one recipe call in the user's Python env (SPEC §8).

This file runs inside the recipe env, not the backend env, so it imports only
the standard library and matplotlib. Two ways to run it:

- As a script: ``python worker.py '<request json>'`` prints one JSON result
  line after the ``PINTU_RESULT`` marker.
- In a kernel: execute the source, then call ``render(request)``.

Request keys: ``root``, ``recipe`` (``module.path:function``), ``params``,
``width_mm``, ``height_mm``, ``out`` (absolute SVG path).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

MM = 25.4
LETTER_ZONE_MM = 5.0
MARKER = "PINTU_RESULT "


def _module_file(module) -> str | None:
    return getattr(module, "__file__", None)


def code_hash(module) -> str | None:
    """sha256 of the recipe module's source file."""
    f = _module_file(module)
    if not f or not Path(f).exists():
        return None
    return hashlib.sha256(Path(f).read_bytes()).hexdigest()


def load(root: str, recipe: str):
    """Imports the recipe function, reloading its module if the file changed.

    Returns:
        (function, module).
    """
    if root not in sys.path:
        sys.path.insert(0, root)
    mod_name, _, fn_name = recipe.partition(":")
    if not fn_name:
        raise ValueError(f"recipe must be 'module:function', got {recipe!r}")
    mod = sys.modules.get(mod_name)
    if mod is None:
        mod = importlib.import_module(mod_name)
    else:
        seen = getattr(mod, "__pintu_hash__", None)
        if seen != code_hash(mod):
            mod = importlib.reload(mod)
    mod.__pintu_hash__ = code_hash(mod)
    fn = mod
    for part in fn_name.split("."):
        fn = getattr(fn, part)
    return fn, mod


def _bbox_mm(bb, fig_h_in: float, dpi: float) -> list[float]:
    """Display-space bbox (pixels, origin bottom-left) to mm, origin top-left."""
    x0, y0, x1, y1 = bb.x0 / dpi, bb.y0 / dpi, bb.x1 / dpi, bb.y1 / dpi
    return [round(v * MM, 2) for v in (x0, fig_h_in - y1, x1, fig_h_in - y0)]


def summarize(fig) -> dict:
    """Text summary of a figure for lint and text-only models.

    Returns:
        Dict with ``size_mm``, ``axes``, ``texts`` and ``overflow``; bboxes are
        ``[x0, y0, x1, y1]`` in mm from the figure's top-left.
    """
    w_in, h_in = fig.get_size_inches()
    if not hasattr(fig.canvas, "get_renderer"):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
    renderer = fig.canvas.get_renderer()
    w_mm, h_mm = w_in * MM, h_in * MM
    axes = []
    for ax in fig.axes:
        if not ax.get_visible():
            continue
        leg = ax.get_legend()
        axes.append({
            "bbox_mm": _bbox_mm(ax.get_window_extent(renderer), h_in, fig.dpi),
            "title": ax.get_title(),
            "xlabel": ax.get_xlabel(),
            "ylabel": ax.get_ylabel(),
            "xlim": [float(v) for v in ax.get_xlim()],
            "ylim": [float(v) for v in ax.get_ylim()],
            "n_xticks": len(ax.get_xticks()),
            "n_yticks": len(ax.get_yticks()),
            "legend": bool(leg and leg.get_visible()),
        })
    texts, overflow = [], []
    from matplotlib.text import Text
    for t in fig.findobj(Text):
        if not t.get_visible() or not t.get_text().strip():
            continue
        bb = _bbox_mm(t.get_window_extent(renderer), h_in, fig.dpi)
        item = {"text": t.get_text()[:80], "fontsize_pt": round(float(t.get_fontsize()), 2), "bbox_mm": bb}
        texts.append(item)
        if bb[0] < -0.05 or bb[1] < -0.05 or bb[2] > w_mm + 0.05 or bb[3] > h_mm + 0.05:
            overflow.append({"text": item["text"], "bbox_mm": bb})
    return {"size_mm": [round(w_mm, 3), round(h_mm, 3)], "axes": axes, "texts": texts,
            "overflow": overflow, "letter_zone_mm": LETTER_ZONE_MM}


def _git_sha(root: str) -> str | None:
    head = Path(root) / ".git" / "HEAD"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (Path(root) / ".git" / ref[5:]).read_text().strip()
        return ref
    except OSError:
        return None


def render(req: dict) -> dict:
    """Calls a recipe at the cell size and saves SVG plus the meta.json sidecar.

    Returns:
        Result dict: ``ok``, ``error``, ``code_hash``, ``summary``, ``seconds``.
    """
    t0 = time.perf_counter()
    try:
        import matplotlib
        matplotlib.use("Agg", force=False)
        import matplotlib.pyplot as plt
        from matplotlib.figure import Figure

        fn, mod = load(req["root"], req["recipe"])
        w, h = float(req["width_mm"]), float(req["height_mm"])
        fig = fn(w, h, **(req.get("params") or {}))
        if not isinstance(fig, Figure):
            raise TypeError(f"recipe returned {type(fig).__name__}, expected matplotlib Figure")
        out = Path(req["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        with matplotlib.rc_context({"svg.fonttype": "none"}):
            fig.savefig(out, format="svg")
        summary = summarize(fig)
        plt.close(fig)
        chash = code_hash(mod)
        meta = {
            "recipe": req["recipe"],
            "params": req.get("params") or {},
            "width_mm": w,
            "height_mm": h,
            "code_hash": chash,
            "git_sha": _git_sha(req["root"]),
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (out.parent / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return {"ok": True, "error": None, "code_hash": chash, "summary": summary,
                "seconds": time.perf_counter() - t0}
    except Exception:
        return {"ok": False, "error": traceback.format_exc(), "code_hash": None, "summary": None,
                "seconds": time.perf_counter() - t0}


if __name__ == "__main__":
    result = render(json.loads(sys.argv[1]))
    sys.stdout.flush()
    print(MARKER + json.dumps(result))
