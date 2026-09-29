import subprocess

import pytest

from pintu.checkpoints import Checkpoints, Conflict
from pintu.project import Project


def git(root, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture(params=["git", "copy"])
def store(request, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n")
    (root / "b.py").write_text("b = 1\n")
    (root / "notes.txt").write_text("user notes\n")
    if request.param == "git":
        git(root, "init", "-q")
        git(root, "add", "a.py", "b.py")
        git(root, "commit", "-qm", "init")
    ck = Checkpoints(Project.open(root))
    assert ck.kind == request.param
    return ck


def edit(ck, turn, rel, text):
    ck.before(turn, rel)
    p = ck.project.root / rel
    if text is None:
        p.unlink()
    else:
        p.write_text(text)
    ck.after(turn, rel)


def test_git_checkpoint_leaves_index_head_and_branch(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("a = 1\n")
    git(root, "init", "-q")
    git(root, "add", "a.py")
    git(root, "commit", "-qm", "init")
    (root / "a.py").write_text("a = 2\n")      # unstaged user change
    (root / "c.py").write_text("c = 1\n")
    git(root, "add", "c.py")                   # staged user change
    before = (git(root, "rev-parse", "HEAD"), git(root, "status", "--porcelain"), git(root, "branch"),
              (root / ".git/index").read_bytes())
    ck = Checkpoints(Project.open(root))
    turn = {"n": 1, "checkpoint": ck.begin("ses_x", 1), "files": {}}
    edit(ck, turn, "a.py", "a = 3\n")
    edit(ck, turn, "new.py", "n = 1\n")
    ck.finish(turn)
    after = (git(root, "rev-parse", "HEAD"), git(root, "status", "--porcelain"), git(root, "branch"),
             (root / ".git/index").read_bytes())
    assert before[0] == after[0] and before[2] == after[2] and before[3] == after[3]
    ref = turn["checkpoint"]["ref"]
    assert ref == "refs/pintu/checkpoints/ses_x/1"
    snap = turn["checkpoint"]["commit"]
    assert git(root, "show", f"{snap}:a.py") == "a = 2\n"   # the working tree, not HEAD
    assert git(root, "show", f"{ref}:before/a.py") == "a = 2\n"
    assert git(root, "show", f"{ref}:after/new.py") == "n = 1\n"
    ck.revert([turn])
    assert (root / "a.py").read_text() == "a = 2\n" and not (root / "new.py").exists()
    assert git(root, "status", "--porcelain") == before[1]
    ck.drop(turn)
    assert "refs/pintu" not in git(root, "for-each-ref")


def test_project_in_a_repo_subfolder(tmp_path):
    top = tmp_path / "repo"
    (top / "proj").mkdir(parents=True)
    (top / "proj/a.py").write_text("a = 1\n")
    git(top, "init", "-q")
    git(top, "add", "-A")
    git(top, "commit", "-qm", "init")
    ck = Checkpoints(Project.open(top / "proj"))
    assert ck.kind == "git" and ck.prefix == "proj"
    turn = {"n": 1, "checkpoint": ck.begin("s", 1), "files": {}}
    edit(ck, turn, "a.py", "a = 2\n")
    ck.finish(turn)
    assert [f["path"] for f in ck.diff([turn])] == ["a.py"]
    ck.revert([turn])
    assert (top / "proj/a.py").read_text() == "a = 1\n"


def test_diff_and_revert_only_touched(store):
    root = store.project.root
    t1 = {"n": 1, "checkpoint": store.begin("s", 1), "files": {}}
    edit(store, t1, "a.py", "a = 2\n")
    edit(store, t1, "c.py", "c = 1\n")
    store.finish(t1)
    (root / "notes.txt").write_text("user edited meanwhile\n")  # not touched by the agent
    files = {f["path"]: f for f in store.diff([t1])}
    assert files["a.py"]["status"] == "modified" and "-a = 1\n+a = 2\n" in files["a.py"]["diff"]
    assert files["c.py"]["status"] == "added" and "--- /dev/null" in files["c.py"]["diff"]
    out = store.revert([t1])
    assert sorted(out["restored"]) == ["a.py", "c.py"] and out["conflicts"] == []
    assert (root / "a.py").read_text() == "a = 1\n" and not (root / "c.py").exists()
    assert (root / "notes.txt").read_text() == "user edited meanwhile\n"


def test_conflict_detection(store):
    root = store.project.root
    t1 = {"n": 1, "checkpoint": store.begin("s", 1), "files": {}}
    edit(store, t1, "a.py", "a = 2\n")
    edit(store, t1, "b.py", "b = 2\n")
    store.finish(t1)
    (root / "a.py").write_text("a = 99  # user\n")
    with pytest.raises(Conflict) as e:
        store.revert([t1])
    assert e.value.conflicts == [{"path": "a.py", "turn": 1, "reason": "changed after the agent's edit"}]
    assert (root / "b.py").read_text() == "b = 2\n"  # fail: nothing changed
    out = store.revert([t1], "skip")
    assert out["restored"] == ["b.py"] and (root / "a.py").read_text() == "a = 99  # user\n"
    assert (root / "b.py").read_text() == "b = 1\n"


def test_staged_revert(store):
    root = store.project.root
    turns = []
    for n, text in ((1, "a = 2\n"), (2, "a = 3\n"), (3, "a = 4\n")):
        t = {"n": n, "checkpoint": store.begin("s", n), "files": {}}
        edit(store, t, "a.py", text)
        if n == 2:
            edit(store, t, "b.py", None)
        store.finish(t)
        turns.append(t)
    assert "-a = 1\n+a = 4\n" in store.diff(turns)[0]["diff"]
    assert {f["path"]: f["status"] for f in store.diff(turns)}["b.py"] == "deleted"
    store.revert(turns[1:])  # back to the end of turn 1
    assert (root / "a.py").read_text() == "a = 2\n" and (root / "b.py").read_text() == "b = 1\n"
    store.revert(turns[:1])
    assert (root / "a.py").read_text() == "a = 1\n"
