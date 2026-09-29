import asyncio
import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client

from pintu import mcp_server
from pintu.llm import LLM, Profile
from pintu.project import Project
from pintu.recipes import SubprocessRunner
from pintu.server import State, create_app

from test_agent import FakeClient, native

FIXTURE = Path(__file__).parent / "fixtures" / "agent"
RECIPE = "recipes/dist.py"


@pytest.fixture
def project(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(FIXTURE, dst)
    src = (dst / RECIPE).read_text()
    src = src.replace("def boxes(w, h, n=40):",
                      "@panel(min_size=(40, 25), max_size=(100, 80))\ndef boxes(w, h, n=40):")
    (dst / RECIPE).write_text("from pintu_sdk import panel\n" + src)
    return Project.open(dst)


def app_for(project, replies, **kw):
    client = FakeClient(replies)
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=True), client=client)
    return create_app(project, watch=False, runner=SubprocessRunner(project), llm_factory=lambda: llm, **kw), client


def idle(c, timeout=60):
    t = time.time()
    while c.get("/api/sessions/status").json():
        assert time.time() - t < timeout
        time.sleep(0.05)


def test_session_rest_and_ws(project):
    app, fake = app_for(project, [native(calls=[("edit_file", {"path": RECIPE, "old_string": 'ax.set_ylabel("Response (a.u.)")',
                                                               "new_string": 'ax.set_ylabel("Response")'})]),
                                  native("done")])
    with TestClient(app) as c, c.websocket_connect("/api/ws") as ws:
        r = c.post("/api/sessions", json={"kind": "prompt", "board": "eval", "panel": "boxes", "prompt": "rename"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert sid.startswith("ses_") and r.json()["status"]["type"] == "busy"
        seen = []
        while not (seen and seen[-1].get("type") == "session.status" and seen[-1]["status"]["type"] == "idle"):
            m = ws.receive_json()
            if m["type"].startswith(("session.", "part.")):
                seen.append(m)
        types = [m["type"] for m in seen]
        assert types[0] == "session.created" and "part.added" in types and "part.updated" in types
        assert c.get("/api/sessions").json()[0]["id"] == sid
        assert c.get("/api/sessions", params={"panel": "trends"}).json() == []
        got = c.get(f"/api/sessions/{sid}").json()
        assert [p["type"] for p in got["parts"]] == ["text", "tool", "text", "patch"]
        d = c.get(f"/api/sessions/{sid}/diff").json()
        assert d["files"][0]["path"] == RECIPE and "+    ax.set_ylabel(\"Response\")" in d["diff"]
        assert c.post(f"/api/sessions/{sid}/prompt", json={"prompt": " "}).status_code == 400
        r = c.post(f"/api/sessions/{sid}/revert", json={})
        assert r.status_code == 200 and r.json()["restored"] == [RECIPE]
        assert 'ax.set_ylabel("Response (a.u.)")' in (project.root / RECIPE).read_text()
        assert c.get("/api/sessions/ses_nope").status_code == 404
        assert c.post("/api/sessions", json={"kind": "promote", "path": "boards/eval.board.yaml"}).status_code == 400


def test_resize_outside_range_starts_adapt(project):
    app, fake = app_for(project, [native("adapted")])
    with TestClient(app) as c:
        b = c.get("/api/boards/eval").json()
        boxes = next(p for p in b["panels"] if p["id"] == "boxes")
        assert boxes["sizeRange"] == {"min": [40.0, 25.0], "max": [100.0, 80.0], "outside": False}
        assert next(p for p in b["panels"] if p["id"] == "trends")["sizeRange"] is None
        r = c.post("/api/boards/eval/ops", json={"ops": [{"op": "set_cell", "id": "boxes", "cell": [0, 22, 36, 33]}]})
        assert r.status_code == 200
        started = r.json()["adaptSessions"]
        assert len(started) == 1
        assert next(p for p in r.json()["panels"] if p["id"] == "boxes")["sizeRange"]["outside"] is True
        idle(c)
        s = c.get(f"/api/sessions/{started[0]}").json()["session"]
        assert s["kind"] == "adapt" and s["panel"] == "boxes" and s["size"][0] > 170
        body = fake.requests[0]["messages"][1]["content"][0]["text"]
        assert "Adapt panel boxes" in body and "max_size=[100.0, 80.0]; the new size is outside it" in body
        # inside the range: no session
        r = c.post("/api/boards/eval/ops", json={"ops": [{"op": "set_cell", "id": "boxes", "cell": [0, 22, 18, 33]}]})
        assert r.json()["adaptSessions"] == []


def test_promote_source_endpoint(project):
    (project.root / "out").mkdir()
    (project.root / "out/p.png").write_bytes(b"x")
    (project.root / "out/p.png.meta.json").write_text(json.dumps({"recipe": "recipes.dist:boxes", "params": {"n": 5}}))
    app, _ = app_for(project, [])
    with TestClient(app) as c:
        r = c.get("/api/promote/source", params={"path": "out/p.png"}).json()
        assert r["script"] == RECIPE and r["params"] == {"n": 5} and r["suggestedRecipe"] == "recipes.p:p"
        assert c.get("/api/promote/source", params={"path": "nope.png"}).status_code == 404


def test_llm_status(project):
    app, _ = app_for(project, [])
    with TestClient(app) as c:
        assert c.get("/api/llm").json() == {"configured": True, "profile": "fake", "model": "m", "tools": True,
                                            "vision": False}

    def missing():
        raise RuntimeError("no LLM config at /nowhere/llm.toml")
    app = create_app(project, watch=False, runner=SubprocessRunner(project), llm_factory=missing)
    with TestClient(app) as c:
        assert c.get("/api/llm").json() == {"configured": False, "error": "no LLM config at /nowhere/llm.toml"}
        r = c.post("/api/sessions", json={"kind": "prompt", "prompt": "hi"})
        assert r.status_code == 400 and "no LLM config" in r.json()["detail"]


def _text(res):
    return "".join(getattr(p, "text", "") for p in res.content)


def test_mcp_tools_in_process(project):
    async def go():
        state = State(project, SubprocessRunner(project))
        state.renders.start()
        try:
            async with Client(mcp_server.build(state)) as c:
                names = {t.name for t in (await c.list_tools()).tools}
                assert names == {"get_board", "render_panel", "lint"}
                board = json.loads(_text(await c.call_tool("get_board", {"board": "eval"})))
                assert [p["id"] for p in board["panels"]][-1] == "boxes"
                assert board["panels"][-1]["plot_mm"][1] < board["panels"][-1]["cell_mm"][3]
                r = await c.call_tool("set_cell", {"board": "eval", "panel": "boxes", "cell": [0, 22, 19, 33]})
                assert r.is_error
            async with Client(mcp_server.build(state, layout=True)) as c:
                names = {t.name for t in (await c.list_tools()).tools}
                assert names == {"get_board", "set_cell", "render_panel", "lint"}
                r = await c.call_tool("set_cell", {"board": "eval", "panel": "boxes", "cell": [0, 22, 19, 33]})
                assert not r.is_error and json.loads(_text(r))["panel"]["cell"] == [0, 22, 19, 33]
                bad = await c.call_tool("set_cell", {"board": "eval", "panel": "boxes", "cell": [0, 0, 19, 33]})
                assert bad.is_error and "overlapping" in _text(bad)
                r = await c.call_tool("render_panel", {"board": "eval", "panel": "boxes", "w": 50, "h": 30,
                                                       "image": True})
                assert "render ok at 50 x 30 mm" in _text(r) and r.content[-1].type == "image"
                lint = json.loads(_text(await c.call_tool("lint", {"board": "eval", "panel": "boxes"})))
                assert list(lint["panels"]) == ["boxes"]
                res = await c.read_resource("pintu://boards")
                assert json.loads(res.contents[0].text) == ["eval"]
            state.writer.submit(lambda: None).result()
            assert "cell: [0, 22, 19, 33]" in project.board_path("eval").read_text()
        finally:
            await state.renders.stop()
            state.writer.shutdown(wait=True)

    asyncio.run(go())


def test_mcp_over_http(project):
    app, _ = app_for(project, [], mcp_layout=True)
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)

    async def go():
        async with Client(f"http://127.0.0.1:{port}/mcp") as c:
            r = await c.call_tool("set_cell", {"board": "eval", "panel": "boxes", "cell": [0, 22, 19, 33]})
            return json.loads(_text(r))

    try:
        out = asyncio.run(go())
        assert out["panel"]["cell"] == [0, 22, 19, 33] and out["adaptSessions"] == []
        assert app.state.pintu.boards["eval"].panel("boxes")["cell"] == [0, 22, 19, 33]
    finally:
        server.should_exit = True
        t.join(10)


def test_mcp_stdio(project):
    from mcp import StdioServerParameters

    exe = str(Path(sys.executable).with_name("pintu"))
    params = StdioServerParameters(command=exe, args=["mcp", "--project", str(project.root), "--allow-layout"])

    async def go():
        async with Client(params) as c:
            board = json.loads(_text(await c.call_tool("get_board", {"board": "eval"})))
            r = await c.call_tool("set_cell", {"board": "eval", "panel": "boxes", "cell": [0, 22, 19, 33]})
            return board, json.loads(_text(r))

    board, out = asyncio.run(go())
    assert board["page"]["grid"] == [36, 36] and out["panel"]["cell"] == [0, 22, 19, 33]
    assert "cell: [0, 22, 19, 33]" in project.board_path("eval").read_text()
