import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pintu import agent as ag
from pintu.llm import LLM, Profile
from pintu.project import Project
from pintu.recipes import SubprocessRunner
from pintu.renders import Renders
from pintu.sessions import SessionError, SessionManager, promote_source

from test_agent import FakeClient, native

FIXTURE = Path(__file__).parent / "fixtures" / "agent"
RECIPE = "recipes/dist.py"
OLD = 'ax.set_ylabel("Response (a.u.)")'


@pytest.fixture(params=["git", "copy"])
def project(request, tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(FIXTURE, dst)
    if request.param == "git":
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "fixture"]):
            subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=dst, check=True)
    return Project.open(dst)


class Blocking:
    """A client whose requests wait until released; records them."""

    def __init__(self, then=None):
        self.release = None
        self.requests = []
        self.then = list(then or [])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw)))
        if not self.then:
            self.release = asyncio.Event()
            await self.release.wait()
        msg = self.then.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))


def manager(project, client, events=None, **kw):
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=True), client=client)

    async def notify(msg):
        if events is not None:
            events.append(msg)

    renders = Renders(project, SubprocessRunner(project), ag._no_board)
    return SessionManager(project, renders, lambda: llm, notify=notify, **kw)


def edit_reply(old, new, path=RECIPE):
    return native(calls=[("edit_file", {"path": path, "old_string": old, "new_string": new})])


def test_lifecycle_parts_events_and_accept(project):
    events = []
    client = FakeClient([native(calls=[("read_file", {"path": RECIPE})], reasoning="look first"),
                         edit_reply(OLD, 'ax.set_ylabel("Response")'), native("Renamed the label.")])
    mgr = manager(project, client, events)

    async def go():
        s = await mgr.create("prompt", "eval", "boxes", "rename the y label")
        assert mgr.status() == {s["id"]: {"type": "busy"}}
        s = await mgr.wait(s["id"])
        await mgr.flush()
        return s

    s = asyncio.run(go())
    assert s["status"] == {"type": "idle"} and s["result"]["stopped"] == "done" and s["steps"] == 3
    assert s["turns"][0]["state"] == "open" and list(s["turns"][0]["files"]) == [RECIPE]
    parts = mgr.get(s["id"])["parts"]
    assert [p["type"] for p in parts] == ["text", "reasoning", "tool", "tool", "text", "patch"]
    assert parts[0]["role"] == "user" and parts[4]["text"] == "Renamed the label."
    assert parts[2]["state"]["status"] == "done" and "def boxes" in parts[2]["state"]["output"]
    assert parts[3]["tool"] == "edit_file" and parts[3]["input"]["path"] == RECIPE
    assert parts[5]["files"][0]["path"] == RECIPE and '+    ax.set_ylabel("Response")' in parts[5]["files"][0]["diff"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "session.created" and "part.updated" in kinds
    st = [e["status"]["type"] for e in events if e["type"] == "session.status"]
    assert st[0] == "busy" and st[-1] == "idle"
    assert mgr.diff(s["id"])["diff"] == parts[5]["files"][0]["diff"]
    out = mgr.accept(s["id"])
    assert out["accepted"] == [1] and s["turns"][0]["state"] == "accepted"
    assert 'ax.set_ylabel("Response")' in (project.root / RECIPE).read_text()
    assert mgr.diff(s["id"])["files"] == []


def test_turns_staged_revert_and_conflict(project):
    client = FakeClient([edit_reply(OLD, 'ax.set_ylabel("R1")'), native("one"),
                         edit_reply('ax.set_ylabel("R1")', 'ax.set_ylabel("R2")'), native("two")])
    mgr = manager(project, client)
    orig = (project.root / RECIPE).read_text()

    async def go():
        s = await mgr.create("prompt", "eval", "boxes", "first")
        await mgr.wait(s["id"])
        await mgr.prompt(s["id"], "second")
        return await mgr.wait(s["id"])

    s = asyncio.run(go())
    assert [t["state"] for t in s["turns"]] == ["open", "open"]
    second = client.requests[2]["messages"]
    assert second[-1] == {"role": "user", "content": "second"} and any(m["role"] == "tool" for m in second)
    out = mgr.revert(s["id"], turn=2)
    assert out["reverted"] == [2] and 'ax.set_ylabel("R1")' in (project.root / RECIPE).read_text()
    (project.root / RECIPE).write_text((project.root / RECIPE).read_text() + "# user\n")
    with pytest.raises(SessionError) as e:
        mgr.revert(s["id"])
    assert e.value.status == 409 and e.value.detail["conflicts"][0]["path"] == RECIPE
    assert (project.root / RECIPE).read_text().endswith("# user\n")
    mgr.revert(s["id"], conflicts="overwrite")
    assert (project.root / RECIPE).read_text() == orig
    assert [t["state"] for t in s["turns"]] == ["reverted", "reverted"]


def test_abort_keeps_history_valid(project):
    client = Blocking()
    mgr = manager(project, client)

    async def go():
        s = await mgr.create("prompt", "eval", "boxes", "slow")
        while not client.release:
            await asyncio.sleep(0.01)
        await mgr.abort(s["id"])
        client.then = [native("fine")]
        await mgr.prompt(s["id"], "again")
        return await mgr.wait(s["id"])

    s = asyncio.run(go())
    assert s["status"]["type"] == "idle" and s["result"]["stopped"] == "done"
    assert [t["outcome"] for t in s["turns"]] == ["aborted", "done"]
    msgs = client.requests[-1]["messages"]
    assert msgs[-2]["content"] == "[The user aborted this turn.]" and msgs[-1]["content"] == "again"


def test_retry_status(project):
    class Flaky(FakeClient):
        async def _create(self, **kw):
            if not getattr(self, "failed", False):
                self.failed = True
                raise type("InternalServerError", (Exception,), {"status_code": 503})("busy")
            return await super()._create(**kw)

    events = []
    mgr = manager(project, Flaky([native("ok")]), events)
    mgr.retry_delays = (0.0,)

    async def go():
        s = await mgr.create("prompt", "eval", None, "hi")
        s = await mgr.wait(s["id"])
        await mgr.flush()
        return s

    s = asyncio.run(go())
    st = [e["status"] for e in events if e["type"] == "session.status"]
    assert [x["type"] for x in st] == ["busy", "retry", "busy", "idle"] and st[1]["attempt"] == 1
    assert s["result"]["stopped"] == "done" and s["turns"][0]["state"] == "empty"


def test_children_one_per_panel_and_concurrency(project):
    a, b = Blocking(), Blocking()
    mgr = manager(project, a)
    clients = iter([a, b, FakeClient([native("c")])])
    mgr.llm_factory = lambda: LLM(Profile("fake", "http://x/v1", "m", tools=True), client=next(clients))

    async def go():
        parent = await mgr.create("prompt", "eval", "boxes", "parent")
        with pytest.raises(SessionError) as e:
            await mgr.create("prompt", "eval", "boxes", "another")
        assert e.value.status == 409
        child = await mgr.create("prompt", panel="boxes", prompt="subtask", parent_id=parent["id"])
        other = await mgr.create("prompt", "eval", "trends", "other panel")
        while not (a.release and b.release):
            await asyncio.sleep(0.01)
        assert set(mgr.status()) == {parent["id"], child["id"], other["id"]}
        await mgr.abort(parent["id"])
        other = await mgr.wait(other["id"])
        return parent, child, other

    parent, child, other = asyncio.run(go())
    assert child["parentId"] == parent["id"] and child["board"] == "eval"
    assert parent["result"]["stopped"] == child["result"]["stopped"] == "aborted"
    assert other["result"]["stopped"] == "done"


def test_persistence_and_restart(project):
    mgr = manager(project, FakeClient([edit_reply(OLD, 'ax.set_ylabel("P")'), native("done")]))

    async def first():
        s = await mgr.create("prompt", "eval", "boxes", "persist me")
        return await mgr.wait(s["id"])

    s = asyncio.run(first())
    client = FakeClient([native("continued")])
    again = manager(project, client)
    assert again.get(s["id"])["parts"] == mgr.get(s["id"])["parts"]

    async def resume():
        await again.prompt(s["id"], "more")
        return await again.wait(s["id"])

    asyncio.run(resume())
    sent = client.requests[0]["messages"]
    assert sent[1]["content"][0]["text"].startswith("## Task\n\npersist me") and sent[-1]["content"] == "more"
    again.revert(s["id"])
    assert OLD in (project.root / RECIPE).read_text()

    blocking = Blocking()
    busy = manager(project, blocking)

    async def crash():
        s = await busy.create("prompt", "eval", "boxes", "interrupted")
        while not blocking.release:
            await asyncio.sleep(0.01)
        reloaded = manager(project, FakeClient([]))  # as after a restart, while the turn runs
        await busy.abort(s["id"])
        return reloaded.get_session(s["id"])

    r = asyncio.run(crash())
    assert r["status"] == {"type": "idle"} and r["turns"][0]["outcome"] == "interrupted"


def test_adapt_entry_point(project):
    client = FakeClient([native("nothing to do")])
    mgr = manager(project, client)

    async def go():
        s = await mgr.create("adapt", "eval", "boxes", size=[44, 55], old_size=[89, 55])
        return await mgr.wait(s["id"])

    s = asyncio.run(go())
    body = client.requests[0]["messages"][1]["content"][0]["text"]
    assert "Adapt panel boxes to its new size, 44 x 55 mm (it was designed for 89 x 55 mm)" in body
    assert "New size: 44 x 55 mm" in body and s["kind"] == "adapt"
    with pytest.raises(SessionError):
        asyncio.run(mgr.create("adapt", "eval", None))


def test_promote_entry_point(project):
    root = project.root
    (root / "scripts").mkdir()
    (root / "scripts/scratch.py").write_text("import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('out/p1.pdf')\n")
    (root / "out").mkdir()
    (root / "out/p1.pdf").write_bytes(b"%PDF-1.4")
    (root / "out/p1.pdf.meta.json").write_text(json.dumps(
        {"recipe": None, "params": {"k": 2}, "width_mm": 60, "height_mm": 40, "script": "scripts/scratch.py"}))
    info = promote_source(project, "out/p1.pdf")
    assert info["script"] == "scripts/scratch.py" and info["fields"]["recipe"] == "recipes.p1:p1"
    code = ("from matplotlib.figure import Figure\n\n\ndef p1(w, h, k=2):\n"
            "    fig = Figure(figsize=(w / 25.4, h / 25.4))\n    fig.add_subplot().plot([1, k])\n    return fig\n")
    client = FakeClient([native(calls=[("write_file", {"path": "recipes/p1.py", "content": code})]),
                         native(calls=[("render_recipe", {"recipe": "recipes.p1:p1", "w": 60, "h": 40})]),
                         native("Wrote it.\nRECIPE: recipes.p1:p1")])
    mgr = manager(project, client)

    async def go():
        s = await mgr.create("promote", path="out/p1.pdf")
        return await mgr.wait(s["id"])

    s = asyncio.run(go())
    body = client.requests[0]["messages"][1]["content"][0]["text"]
    assert "The script scripts/scratch.py drew out/p1.pdf with params {\"k\": 2}" in body
    assert "def p1(w, h, k=2)" in body
    assert [t["function"]["name"] for t in client.requests[0]["tools"]][-2:] == ["write_file", "render_recipe"]
    parts = mgr.get(s["id"])["parts"]
    assert "render ok at 60 x 40 mm" in parts[2]["state"]["output"]
    assert s["result"]["recipe"] == "recipes.p1:p1"
    assert parts[-1]["files"][0]["status"] == "added"
    assert (root / "scripts/scratch.py").read_text().startswith("import matplotlib")
    mgr.revert(s["id"])
    assert not (root / "recipes/p1.py").exists()
    (root / "out/p2.pdf").write_bytes(b"%PDF-1.4")
    with pytest.raises(SessionError, match="not known"):
        promote_source(project, "out/p2.pdf")


def test_no_profile_creates_nothing(project):
    mgr = manager(project, FakeClient([]))

    def missing():
        raise RuntimeError("no llm.toml")

    mgr.llm_factory = missing
    with pytest.raises(SessionError, match="cannot start the agent: no llm.toml"):
        asyncio.run(mgr.create("prompt", "eval", "boxes", "hi"))
    assert mgr.list() == [] and not (project.root / ".pintu/sessions").exists()
