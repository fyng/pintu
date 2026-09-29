import time

import pytest
from fastapi.testclient import TestClient

from pintu.project import Project
from pintu.server import create_app


@pytest.fixture
def client(demo):
    with TestClient(create_app(Project.open(demo), watch=False)) as c:
        yield c


def flush(client):
    """Waits for queued board and build writes."""
    client.app.state.pintu.writer.submit(lambda: None).result()


def test_project_and_board(client):
    assert client.get("/api/project").json()["boards"] == ["demo", "recipes"]
    b = client.get("/api/boards/demo").json()
    assert b["page"]["grid"] == [36, 36]
    assert [p["letter"] for p in b["panels"]] == ["a", "b", "c", "d"]
    assert b["panels"][3]["kind"] == "raster"
    assert client.get("/api/boards/nope").status_code == 404


def test_ops_save_and_push_preview(client, demo):
    with client.websocket_connect("/api/ws") as ws:
        r = client.post("/api/boards/demo/ops", json={"ops": [{"op": "set_cell", "id": "bars", "cell": [0, 18, 6, 36]}]})
        assert r.status_code == 200, r.text
        assert r.json()["panels"][2]["cell"] == [0, 18, 6, 36]
        m1, m2 = ws.receive_json(), ws.receive_json()
        assert m1["type"] == "board" and m2["type"] == "preview"
        assert m2["svg"].startswith("<svg") and m2["rev"] == m1["board"]["rev"]
    flush(client)
    text = (demo / "boards/demo.board.yaml").read_text()
    assert (demo / "boards/build/demo.pdf").read_bytes().startswith(b"%PDF")
    assert "cell: [0, 18, 6, 36]" in text
    assert "# raster: sits below the letter zone" in text


def test_invalid_ops_are_rejected_atomically(client, demo):
    before = (demo / "boards/demo.board.yaml").read_text()
    r = client.post("/api/boards/demo/ops", json={"ops": [
        {"op": "set_cell", "id": "bars", "cell": [0, 18, 6, 36]},
        {"op": "set_cell", "id": "lines", "cell": [0, 0, 20, 18]},
    ]})
    assert r.status_code == 400 and "overlapping" in r.json()["detail"]
    assert (demo / "boards/demo.board.yaml").read_text() == before
    assert client.post("/api/boards/demo/ops", json={"ops": [{"op": "add", "cell": [0, 0, 1, 1], "file": "../x.pdf"}]}).status_code == 400


def test_add_split_letter(client):
    client.post("/api/boards", json={"name": "fig", "height": 100})
    r = client.post("/api/boards/fig/ops", json={"ops": [{"op": "add", "cell": [0, 0, 36, 12], "file": "plots/lines.pdf"}]})
    assert r.json()["panels"][0]["id"] == "lines"
    r = client.post("/api/boards/fig/ops", json={"ops": [{"op": "split", "id": "lines", "n": 5}]}).json()
    assert len(r["panels"]) == 5 and r["opWarnings"]
    r = client.post("/api/boards/fig/ops", json={"ops": [{"op": "set_letter", "id": "lines-2", "letter": None}]}).json()
    assert [p["letter"] for p in r["panels"]] == ["a", None, "b", "c", "d"]
    assert client.get("/api/boards/fig/preview.pdf").content.startswith(b"%PDF")
    assert client.get("/api/boards/fig/preview.svg").content.startswith(b"<svg")
    assert client.post("/api/boards", json={"name": "fig"}).status_code == 409
    assert client.post("/api/boards", json={"name": "../x"}).status_code == 400


def test_file_browser_confined(client):
    names = [e["name"] for e in client.get("/api/files", params={"path": "plots"}).json()["entries"]]
    assert "lines.pdf" in names
    for bad in ["..", "../..", "/etc", "plots/../../"]:
        assert client.get("/api/files", params={"path": bad}).status_code == 400
        assert client.get("/api/raw", params={"path": bad + "/passwd"}).status_code == 400
    assert client.get("/api/raw", params={"path": "plots/scatter.svg"}).status_code == 200
    assert client.get("/api/thumb", params={"path": "plots/lines.pdf"}).content.startswith(b"<svg")


def test_external_edit_reloads(demo):
    with TestClient(create_app(Project.open(demo))) as c:
        with c.websocket_connect("/api/ws") as ws:
            time.sleep(0.5)
            path = demo / "boards/demo.board.yaml"
            path.write_text(path.read_text().replace("[0, 18, 12, 36]", "[0, 18, 9, 36]"))
            m = ws.receive_json()
            assert m["type"] == "board"
            assert m["board"]["panels"][2]["cell"] == [0, 18, 9, 36]
