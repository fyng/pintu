import json
import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from pintu.catalog import Catalog, read_sidecar
from pintu.project import Project
from pintu.server import create_app


def _linked(root, rel, recipe, params, src="plots/lines.pdf"):
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(root / src, out)
    meta = {"recipe": recipe, "params": params, "width_mm": 89, "height_mm": 55,
            "code_hash": None, "git_sha": None, "created": "2026-09-29T00:00:00+00:00"}
    (out.parent / (out.name + ".meta.json")).write_text(json.dumps(meta))
    return out


@pytest.fixture
def cohort(demo):
    for i in range(1, 13):
        _linked(demo, f"scratch/P{i:03d}.pdf", "recipes.timeline:timeline",
                {"patient": f"P{i:03d}", "arm": "AB"[i % 2], "seed": i % 3})
    return demo


def test_read_sidecar_per_file_and_folder(demo):
    out = _linked(demo, "scratch/a.pdf", "m:f", {"n": 1})
    assert read_sidecar(out)["params"] == {"n": 1}
    assert read_sidecar(demo / "plots/lines.pdf") is None
    d = demo / "pintu_out/m.f/abc"
    d.mkdir(parents=True)
    shutil.copy(demo / "plots/scatter.svg", d / "89x55.5.svg")
    (d / "meta.json").write_text(json.dumps({"recipe": "m:f", "params": {"n": 2}, "width_mm": 40, "height_mm": 30}))
    m = read_sidecar(d / "89x55.5.svg")
    assert (m["recipe"], m["width_mm"], m["height_mm"]) == ("m:f", 89, 55.5)


def test_scan_incremental(cohort):
    cat = Catalog(Project.open(cohort))
    assert cat.scan() == 16  # 12 linked + 4 plain plots
    assert cat.scan() == 0
    p = cohort / "scratch/P001.pdf"
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10**9))
    side = cohort / "scratch/P002.pdf.meta.json"
    meta = json.loads(side.read_text())
    meta["params"]["arm"] = "C"
    side.write_text(json.dumps(meta))
    (cohort / "plots/bars.pdf").unlink()
    assert cat.scan() == 3
    assert cat.query(filters={"arm": ["C"]})["total"] == 1
    assert cat.query(recipe="")["total"] == 3
    cat.close()
    assert Catalog(Project.open(cohort)).scan() == 0  # persisted in .pintu/catalog.sqlite
    assert (cohort / ".pintu/catalog.sqlite").is_file()


def test_query_groups_filters_pages(cohort):
    cat = Catalog(Project.open(cohort))
    cat.scan()
    r = cat.query(limit=5)
    assert r["total"] == 16 and len(r["items"]) == 5
    assert r["groups"] == [{"recipe": "recipes.timeline:timeline", "count": 12}, {"recipe": None, "count": 4}]
    assert r["items"][0]["path"] == "scratch/P001.pdf" and r["items"][0]["params"]["arm"] == "B"
    pages = [cat.query(offset=o, limit=5)["items"] for o in (0, 5, 10, 15)]
    paths = [i["path"] for page in pages for i in page]
    assert len(paths) == 16 == len(set(paths)) and paths[-1].startswith("plots/")
    three = cat.query(recipe="recipes.timeline:timeline", filters={"patient": ["P002", "P005", "P009"]})
    assert [i["path"] for i in three["items"]] == ["scratch/P002.pdf", "scratch/P005.pdf", "scratch/P009.pdf"]
    both = cat.query(filters={"arm": ["A"], "seed": ["0"]})  # keys AND, values OR
    assert [i["params"]["patient"] for i in both["items"]] == ["P006", "P012"]
    assert cat.query(q="P01")["total"] == 3
    f = cat.facets("recipes.timeline:timeline")
    assert f["arm"] == [{"value": "A", "count": 6}, {"value": "B", "count": 6}]
    assert [v["value"] for v in f["seed"]] == ["0", "1", "2"]


def test_folder_sidecar_collapses_sizes(demo):
    d = demo / "pintu_out/m.f/abc"
    d.mkdir(parents=True)
    for i, name in enumerate(["40x30.svg", "80x30.svg"]):
        shutil.copy(demo / "plots/scatter.svg", d / name)
        os.utime(d / name, ns=(0, (i + 1) * 10**18))
    (d / "meta.json").write_text(json.dumps({"recipe": "m:f", "params": {"n": 2}}))
    cat = Catalog(Project.open(demo))
    cat.scan()
    r = cat.query(recipe="m:f")
    assert r["total"] == 1 and r["items"][0]["path"] == "pintu_out/m.f/abc/80x30.svg"
    assert r["items"][0]["width_mm"] == 80


def test_scan_paths_and_thumbs(cohort):
    (cohort / "pintu.toml").write_text('[gallery]\npaths = ["scratch", "../outside", "nope"]\n')
    cat = Catalog(Project.open(cohort))
    cat.scan()
    assert cat.query()["total"] == 12
    png = cat.thumb("scratch/P001.pdf")
    assert png.read_bytes().startswith(b"\x89PNG") and png.parent == cohort / ".pintu/thumbs"
    assert cat.thumb("scratch/P001.pdf") == png
    with pytest.raises(KeyError):
        cat.thumb("plots/lines.pdf")


def test_gallery_api_and_recipe_drop(cohort):
    with TestClient(create_app(Project.open(cohort), watch=False)) as c:
        r = c.get("/api/gallery", params={"recipe": "recipes.timeline:timeline",
                                          "filters": json.dumps({"patient": ["P003"]})}).json()
        assert r["total"] == 1
        item = r["items"][0]
        t = c.get("/api/gallery/thumb", params={"path": item["path"], "v": item["version"]})
        assert t.status_code == 200 and t.headers["content-type"] == "image/png"
        assert "immutable" in t.headers["cache-control"]
        assert c.get("/api/gallery", params={"filters": "[1]"}).status_code == 400
        assert "arm" in c.get("/api/gallery/facets", params={"recipe": item["recipe"]}).json()["params"]
        b = c.post("/api/boards/demo/ops", json={"ops": [
            {"op": "remove", "id": "bars"},
            {"op": "add", "cell": [0, 18, 12, 36], "recipe": item["recipe"], "params": item["params"]}]})
        assert b.status_code == 200, b.text
        p = next(p for p in b.json()["panels"] if p["cell"] == [0, 18, 12, 36])
        assert p["source"] == {"recipe": item["recipe"]} and p["params"] == item["params"]


def test_live_update(demo):
    with TestClient(create_app(Project.open(demo))) as c:
        with c.websocket_connect("/api/ws") as ws:
            time.sleep(0.5)
            _linked(demo, "scratch/new.pdf", "m:f", {"n": 9})
            assert ws.receive_json() == {"type": "gallery"}
            assert c.get("/api/gallery", params={"recipe": "m:f"}).json()["total"] == 1
