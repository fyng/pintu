"""``save``: writes a figure and its sidecar, so pintu's gallery treats it as linked (SPEC §7)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

MM = 25.4
SIDECAR_SUFFIX = ".meta.json"


def sidecar_path(path: Union[str, Path]) -> Path:
    """The per-file sidecar of an output: ``<name>.meta.json`` beside it."""
    p = Path(path)
    return p.with_name(p.name + SIDECAR_SUFFIX)


def find_root(start: Path) -> Path:
    """The nearest folder at or above ``start`` holding ``pintu.toml``, else the working directory."""
    for d in [start, *start.parents]:
        if (d / "pintu.toml").is_file():
            return d
    return Path.cwd()


def _module_path(root: Path, name: str) -> Optional[Path]:
    if not name or not all(part.isidentifier() for part in name.split(".")):
        return None
    base = root.joinpath(*name.split("."))
    for f in (base.with_suffix(".py"), base / "__init__.py"):
        if f.is_file():
            return f
    return None


def _imported(name: str, path: Path) -> list:
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (SyntaxError, ValueError):
        return []
    pkg = name if path.name == "__init__.py" else name.rpartition(".")[0]
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = pkg.split(".") if pkg else []
                parts = parts[: len(parts) - node.level + 1]
                base = ".".join(parts + ([node.module] if node.module else []))
            if base:
                names.append(base)
            names += [f"{base}.{a.name}" if base else a.name for a in node.names]
    out = []
    for n in names:
        parts = n.split(".")
        out += [".".join(parts[: i + 1]) for i in range(len(parts))]
    return out


def code_hash(root: Union[str, Path], module: str) -> Optional[str]:
    """sha256 over a module and its project-local imports, as pintu's render worker computes it."""
    root = Path(root)
    order: list = []
    seen: set = set()

    def visit(n: str) -> None:
        seen.add(n)
        f = _module_path(root, n)
        if f is None:
            return
        for d in _imported(n, f):
            if d not in seen:
                visit(d)
        order.append((n, f))

    visit(module)
    if not order or order[-1][0] != module:
        return None
    h = hashlib.sha256()
    for n, f in order:
        h.update(n.encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def git_sha(root: Union[str, Path]) -> Optional[str]:
    """The checked-out commit of the git repo at or above ``root``, read from ``.git``."""
    for d in [Path(root), *Path(root).parents]:
        head = d / ".git" / "HEAD"
        if not head.is_file():
            continue
        try:
            ref = head.read_text().strip()
            if ref.startswith("ref: "):
                return (d / ".git" / ref[5:]).read_text().strip()
            return ref
        except OSError:
            return None
    return None


def main_script(root: Path) -> Optional[str]:
    """The running ``__main__`` script as a root-relative ``/`` path, or None if it is outside the root."""
    f = getattr(sys.modules.get("__main__"), "__file__", None)
    if not f:
        return None
    try:
        return Path(f).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def save(fig: Any, path: Union[str, Path], recipe: Optional[str] = None,
         params: Optional[dict] = None, script: Optional[str] = None, **savefig_kwargs: Any) -> Path:
    """Saves a figure and writes its sidecar ``<name>.meta.json``.

    Args:
        fig: A matplotlib Figure (anything with ``savefig`` and ``get_size_inches``).
        path: Output file; the suffix picks the format.
        recipe: ``module.path:function`` that draws this plot as ``fn(w, h, **params)``.
            Without it the file is indexed as unlinked.
        params: JSON-serializable keyword arguments of the recipe call.
        script: Root-relative path of the script that drew the plot, which pintu's
            "Promote to recipe" reads; defaults to the running ``__main__`` file
            when it is under the project root.
        **savefig_kwargs: Passed to ``fig.savefig``.

    Returns:
        The sidecar path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, **savefig_kwargs)
    w, h = fig.get_size_inches()
    root = find_root(path.resolve().parent)
    meta = {
        "recipe": recipe,
        "params": dict(params or {}),
        "width_mm": round(float(w) * MM, 3),
        "height_mm": round(float(h) * MM, 3),
        "code_hash": code_hash(root, recipe.partition(":")[0]) if recipe else None,
        "script": script or main_script(root),
        "git_sha": git_sha(root),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    side = sidecar_path(path)
    tmp = side.with_name(side.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    tmp.replace(side)
    return side
