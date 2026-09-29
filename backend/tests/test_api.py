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


def _wait_cell(c, cell, timeout=5.0):
    state = c.app.state.pintu
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if state.boards["demo"].panel("bars")["cell"] == cell:
            return True
        time.sleep(0.05)
    return False


def test_external_revert_to_earlier_text_reloads(demo):
    path = demo / "boards/demo.board.yaml"
    with TestClient(create_app(Project.open(demo))) as c:
        time.sleep(0.5)
        r = c.post("/api/boards/demo/ops", json={"ops": [{"op": "set_cell", "id": "bars", "cell": [0, 18, 6, 36]}]})
        assert r.status_code == 200, r.text
        flush(c)
        written = path.read_text()
        path.write_text(written.replace("[0, 18, 6, 36]", "[0, 18, 9, 36]"))
        assert _wait_cell(c, [0, 18, 9, 36])
        path.write_text(written)  # hand-revert to the text pintu wrote
        assert _wait_cell(c, [0, 18, 6, 36])


def test_multiples_and_group_ops(client, demo):
    r = client.post("/api/boards/demo/ops", json={"ops": [
        {"op": "remove", "id": "lines"}, {"op": "remove", "id": "scatter"},
        {"op": "add", "cell": [0, 0, 18, 18], "recipe": "recipes.cohort:timeline",
         "params": {"arm": "A", "patient": "S002", "stage": "I"}}]})
    assert r.status_code == 200, r.text
    p = next(q for q in r.json()["panels"] if q["id"] == "timeline-S002")
    assert p["multiplesItem"] == "patient" and "multiples" not in p
    five = [["S002", "S005", "S009", "S010", "S011"]]
    r = client.post("/api/boards/demo/ops", json={"ops": [
        {"op": "set_multiples", "id": "timeline-S002", "item": "patient"},
        {"op": "set_multiples", "id": "timeline-S002", "mosaic": five}]})
    assert r.status_code == 200, r.text
    m = next(q for q in r.json()["panels"] if q["id"] == "timeline-S002")["multiples"]
    assert m["mosaic"] == five and m["share"] == {"x": "all", "y": "all"} and m["minCell"] == [40.0, 16.0]
    assert m["cellMm"][0] < 40 and m["reflow"] == [["S002", "S005"], ["S009", "S010"], ["S011", "."]]
    r = client.post("/api/boards/demo/ops", json={"ops": [{"op": "group", "ids": ["bars", "heatmap"]}]})
    v = r.json()
    assert v["groups"][0]["panels"] == ["bars", "heatmap"] and v["groups"][0]["letter"] == "b"
    assert [(q["group"], q["letter"]) for q in v["panels"] if q["id"] in ("bars", "heatmap")] == \
        [("group", "b"), ("group", None)]
    flush(client)
    text = (demo / "boards/demo.board.yaml").read_text()
    assert "multiples: {item: patient, mosaic: [[S002, S005, S009, S010, S011]]}" in text
    assert "params: {arm: A, stage: I}" in text and "groups:\n  - {id: group, panels: [bars, heatmap]}" in text
