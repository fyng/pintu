"""Project folder: settings, board files and confined path access (SPEC §7)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

SETTINGS = "pintu.toml"
BOARD_SUFFIX = ".board.yaml"
DEFAULT_SETTINGS = """\
# pintu project settings.
[project]
style = "nature"
"""
IMAGE_KINDS = {".pdf": "vector", ".svg": "vector", ".png": "raster", ".jpg": "raster",
               ".jpeg": "raster", ".gif": "raster", ".webp": "raster"}


class PathError(ValueError):
    """A path outside the project root, or malformed."""


@dataclass
class Project:
    """A project folder.

    Attributes:
        root: Absolute, resolved project root.
        settings: Parsed pintu.toml.
    """

    root: Path
    settings: dict = field(default_factory=dict)

    @classmethod
    def open(cls, root: Path | str, create: bool = False) -> "Project":
        """Opens a project, writing pintu.toml and boards/ if `create`."""
        root = Path(root).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"project folder not found: {root}")
        toml = root / SETTINGS
        if create:
            if not toml.exists():
                toml.write_text(DEFAULT_SETTINGS, encoding="utf-8")
            (root / "boards").mkdir(exist_ok=True)
        settings = tomllib.loads(toml.read_text(encoding="utf-8")) if toml.exists() else {}
        return cls(root=root, settings=settings)

    @property
    def boards_dir(self) -> Path:
        """Folder holding board files."""
        return self.root / "boards"

    @property
    def build_dir(self) -> Path:
        """Folder for generated .typ and .pdf files."""
        return self.boards_dir / "build"

    def board_path(self, name: str) -> Path:
        """Path of a board file by name."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise PathError(f"invalid board name {name!r}")
        return self.boards_dir / f"{name}{BOARD_SUFFIX}"

    def board_names(self) -> list[str]:
        """Names of the project's boards."""
        if not self.boards_dir.is_dir():
            return []
        return sorted(p.name[: -len(BOARD_SUFFIX)] for p in self.boards_dir.glob("*" + BOARD_SUFFIX))

    def resolve(self, rel: str) -> Path:
        """Resolves a project-relative '/' path, refusing escapes.

        Raises:
            PathError: The path is absolute, has '..', or resolves outside the
                root (including through symlinks).
        """
        if rel in ("", "."):
            return self.root
        if "\\" in rel or "\x00" in rel:
            raise PathError(f"invalid path {rel!r}")
        pp = PurePosixPath(rel)
        if pp.is_absolute() or ".." in pp.parts or (pp.parts and ":" in pp.parts[0]):
            raise PathError(f"path must be relative to the project root: {rel!r}")
        p = (self.root / pp).resolve()
        if p != self.root and self.root not in p.parents:
            raise PathError(f"path escapes the project root: {rel!r}")
        return p

    def relative(self, path: Path) -> str:
        """The project-relative '/' form of an absolute path inside the root."""
        return Path(path).resolve().relative_to(self.root).as_posix()

    def list_dir(self, rel: str = "") -> list[dict]:
        """Lists a folder: sub-folders first, then files. Skips hidden entries and escapes."""
        d = self.resolve(rel)
        if not d.is_dir():
            raise PathError(f"not a folder: {rel!r}")
        out = []
        for c in d.iterdir():
            if c.name.startswith("."):
                continue
            try:
                r = self.resolve(PurePosixPath(rel, c.name).as_posix() if rel else c.name)
            except PathError:
                continue
            is_dir = r.is_dir()
            out.append({
                "name": c.name,
                "path": PurePosixPath(rel, c.name).as_posix() if rel else c.name,
                "dir": is_dir,
                "kind": None if is_dir else IMAGE_KINDS.get(c.suffix.lower()),
                "size": None if is_dir else r.stat().st_size,
            })
        out.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return out
