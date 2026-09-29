"""Per-turn checkpoints of agent edits: diff, accept and revert (SPEC §9, "Safety and undo").

Each agent turn starts with a checkpoint. In a git repository it is a commit of the
tracked working tree on a hidden ref, ``refs/pintu/checkpoints/<session>/<turn>``,
built with a temporary index, so the user's index, HEAD and branches are untouched.
Outside git it is a folder, ``.pintu/checkpoints/<session>/<turn>/``.

Either way, the content of each file the agent writes is kept just before its first
write in the turn and after each write (git: blobs, reachable from the turn's ref;
otherwise: file copies). A revert restores only those files, and only where the
file on disk still holds what the agent left; a file changed since is a conflict.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .project import Project

REF_PREFIX = "refs/pintu/checkpoints"
COPY_DIR = ".pintu/checkpoints"
_IDENT = {"GIT_AUTHOR_NAME": "pintu", "GIT_AUTHOR_EMAIL": "pintu@localhost",
          "GIT_COMMITTER_NAME": "pintu", "GIT_COMMITTER_EMAIL": "pintu@localhost"}


class Conflict(Exception):
    """A revert would overwrite changes made after the agent's edit.

    Attributes:
        conflicts: ``{path, turn, reason}`` per file.
    """

    def __init__(self, conflicts: list[dict]):
        super().__init__("; ".join(f"{c['path']} (turn {c['turn']}): {c['reason']}" for c in conflicts))
        self.conflicts = conflicts


def digest(data: Optional[bytes]) -> Optional[str]:
    """sha256 of file content; None for a missing file."""
    return None if data is None else hashlib.sha256(data).hexdigest()


class Checkpoints:
    """Checkpoint store of one project.

    Args:
        project: The project.
        use_git: Force git (True) or file copies (False); default: git if the root is in a work tree.

    Attributes:
        kind: ``git`` or ``copy``.
    """

    def __init__(self, project: Project, use_git: Optional[bool] = None):
        self.project = project
        self.top: Optional[Path] = None
        if use_git is not False:
            try:
                out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=project.root,
                                     capture_output=True, text=True, check=True).stdout.strip()
                self.top = Path(out).resolve()
            except (OSError, subprocess.CalledProcessError):
                if use_git:
                    raise
        self.kind = "git" if self.top else "copy"
        self.prefix = project.root.relative_to(self.top).as_posix() if self.top else ""

    # -- git plumbing -------------------------------------------------------

    def _git(self, *args: str, index: Optional[str] = None, data: Optional[bytes] = None) -> bytes:
        env = {**os.environ, **_IDENT}
        if index:
            env["GIT_INDEX_FILE"] = index
        return subprocess.run(["git", *args], cwd=self.top, env=env, input=data,
                              capture_output=True, check=True).stdout

    def _gpath(self, rel: str) -> str:
        return f"{self.prefix}/{rel}" if self.prefix and self.prefix != "." else rel

    def _commit(self, tree: str, parent: Optional[str], msg: str) -> str:
        args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
        return self._git(*args).decode().strip()

    # -- turns --------------------------------------------------------------

    def begin(self, session: str, n: int) -> dict:
        """Takes the checkpoint that starts turn ``n``; returns its record for the session file."""
        if self.kind == "copy":
            d = f"{COPY_DIR}/{session}/{n}"
            (self.project.root / d).mkdir(parents=True, exist_ok=True)
            return {"kind": "copy", "dir": d}
        ref = f"{REF_PREFIX}/{session}/{n}"
        with tempfile.TemporaryDirectory(prefix="pintu-ckpt-") as tmp:
            index = str(Path(tmp) / "index")
            real = self.top / self._git("rev-parse", "--git-path", "index").decode().strip()
            if real.is_file():
                shutil.copyfile(real, index)  # the stat cache makes `add -u` fast
            else:
                with contextlib.suppress(subprocess.CalledProcessError):
                    self._git("read-tree", "HEAD", index=index)
            self._git("add", "-u", "--", self.prefix or ".", index=index)
            tree = self._git("write-tree", index=index).decode().strip()
        try:
            head = self._git("rev-parse", "-q", "--verify", "HEAD").decode().strip()
        except subprocess.CalledProcessError:
            head = None
        commit = self._commit(tree, head, f"pintu checkpoint {session} turn {n}")
        self._git("update-ref", ref, commit)
        return {"kind": "git", "ref": ref, "commit": commit}

    def _put(self, ck: dict, which: str, rel: str, data: Optional[bytes]) -> Optional[str]:
        """Stores content; returns its key (blob sha or copy path), None for a missing file."""
        if data is None:
            return None
        if ck["kind"] == "git":
            return self._git("hash-object", "-w", "--stdin", data=data).decode().strip()
        key = f"{ck['dir']}/{which}/{rel}"
        p = self.project.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def _get(self, ck: dict, key: Optional[str]) -> Optional[bytes]:
        if key is None:
            return None
        if ck["kind"] == "git":
            return self._git("cat-file", "blob", key)
        return (self.project.root / key).read_bytes()

    def _read(self, rel: str) -> Optional[bytes]:
        p = self.project.root / rel
        return p.read_bytes() if p.is_file() else None

    def before(self, turn: dict, rel: str) -> None:
        """Keeps a file's content before the agent's first write to it in this turn."""
        files = turn.setdefault("files", {})
        if rel not in files:
            data = self._read(rel)
            files[rel] = {"before": self._put(turn["checkpoint"], "before", rel, data),
                          "beforeHash": digest(data), "after": None, "afterHash": digest(data)}

    def after(self, turn: dict, rel: str) -> None:
        """Keeps what the agent left in a file after a write."""
        data = self._read(rel)
        f = turn["files"][rel]
        f["after"], f["afterHash"] = self._put(turn["checkpoint"], "after", rel, data), digest(data)

    def finish(self, turn: dict) -> None:
        """Git: commits the kept blobs onto the turn's ref, so gc keeps them."""
        ck = turn["checkpoint"]
        if ck["kind"] != "git" or not turn.get("files"):
            return
        with tempfile.TemporaryDirectory(prefix="pintu-ckpt-") as tmp:
            index = str(Path(tmp) / "index")
            self._git("read-tree", "--empty", index=index)
            for rel, f in turn["files"].items():
                for which in ("before", "after"):
                    if f.get(which):
                        self._git("update-index", "--add", "--cacheinfo", f"100644,{f[which]},{which}/{rel}",
                                  index=index)
            tree = self._git("write-tree", index=index).decode().strip()
        ck["files"] = self._commit(tree, ck["commit"], f"pintu checkpoint files {ck['ref']}")
        self._git("update-ref", ck["ref"], ck["files"])

    def drop(self, turn: dict) -> None:
        """Deletes the checkpoint (after an accept or a revert)."""
        ck = turn["checkpoint"]
        if ck["kind"] == "git":
            with contextlib.suppress(subprocess.CalledProcessError):
                self._git("update-ref", "-d", ck["ref"])
        else:
            shutil.rmtree(self.project.root / ck["dir"], ignore_errors=True)

    # -- diff and revert ----------------------------------------------------

    def diff(self, turns: list[dict]) -> list[dict]:
        """Per file: ``path``, ``status`` (added, modified, deleted) and a unified ``diff``,
        from the first given turn's before to the last one's after. Turns in order."""
        first: dict[str, tuple[dict, dict]] = {}
        last: dict[str, tuple[dict, dict]] = {}
        for t in turns:
            for rel, f in (t.get("files") or {}).items():
                first.setdefault(rel, (t, f))
                last[rel] = (t, f)
        out = []
        for rel in sorted(first):
            (t0, f0), (t1, f1) = first[rel], last[rel]
            if f0["beforeHash"] == f1["afterHash"]:
                continue
            a, b = self._get(t0["checkpoint"], f0["before"]), self._get(t1["checkpoint"], f1["after"])
            status = "added" if a is None else "deleted" if b is None else "modified"
            lines = difflib.unified_diff(
                (a or b"").decode("utf-8", "replace").splitlines(keepends=True),
                (b or b"").decode("utf-8", "replace").splitlines(keepends=True),
                fromfile="/dev/null" if a is None else f"a/{rel}", tofile="/dev/null" if b is None else f"b/{rel}")
            text = "".join(ln if ln.endswith("\n") else ln + "\n\\ No newline at end of file\n" for ln in lines)
            out.append({"path": rel, "status": status, "diff": text})
        return out

    def revert(self, turns: list[dict], conflicts: str = "fail") -> dict:
        """Restores the files of ``turns`` (in order; reverted newest first) to their content before each turn.

        Args:
            turns: The turns to undo.
            conflicts: A file changed since the agent's write: ``fail`` (change nothing and raise),
                ``skip`` (keep it) or ``overwrite``.

        Returns:
            ``restored`` paths and ``conflicts`` (``{path, turn, reason}``).

        Raises:
            Conflict: ``conflicts == "fail"`` and a file changed since the agent's write.
        """
        now: dict[str, Optional[str]] = {}
        found, skip = [], set()
        for t in reversed(turns):
            for rel, f in (t.get("files") or {}).items():
                if rel in skip:
                    continue
                cur = now[rel] if rel in now else digest(self._read(rel))
                if cur != f["afterHash"]:
                    found.append({"path": rel, "turn": t["n"],
                                  "reason": "deleted after the agent's edit" if cur is None
                                  else "changed after the agent's edit"})
                    if conflicts == "skip":
                        skip.add(rel)
                        continue
                now[rel] = f["beforeHash"]
        if found and conflicts == "fail":
            raise Conflict(found)
        restored = []
        for t in reversed(turns):
            for rel, f in (t.get("files") or {}).items():
                if rel in skip:
                    continue
                data = self._get(t["checkpoint"], f["before"])
                p = self.project.root / rel
                if data is None:
                    p.unlink(missing_ok=True)
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    tmp = p.with_name(p.name + ".pintu-revert")
                    tmp.write_bytes(data)
                    tmp.replace(p)
                if rel not in restored:
                    restored.append(rel)
        return {"restored": restored, "conflicts": found}

