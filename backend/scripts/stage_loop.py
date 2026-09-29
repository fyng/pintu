"""B3 exit check at the backend level: a stage 1 -> 3 loop on one figure (SPEC §12.2).

On a fresh copy of ``examples/demo`` (a git repo): picks a gallery item whose script is
known, promotes it to a recipe in a session, places the recipe on a new board, resizes
it outside its size range so an "Adapt to size" session starts, accepts the first
session and reverts the second, and checks the board and files. Uses the real LLM
profile; writes ``<out>/report.json`` and prints each step.

    uv run python scripts/stage_loop.py --profile glm --out /tmp/stage-loop
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from pintu.project import Project
from pintu.server import create_app, default_llm

DEMO = Path(__file__).resolve().parents[2] / "examples" / "demo"
ITEM = "plots/lines.pdf"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=loop@pintu", "-c", "user.name=pintu-loop", *args],
                          cwd=root, capture_output=True, text=True, check=True).stdout


def wait_idle(c: TestClient, sid: str, timeout: float = 3600) -> dict:
    t = time.time()
    while c.get("/api/sessions/status").json().get(sid):
        if time.time() - t > timeout:
            raise TimeoutError(sid)
        time.sleep(1)
    return c.get(f"/api/sessions/{sid}").json()


def wait_render(c: TestClient, board: str, pid: str, timeout: float = 120) -> dict:
    t = time.time()
    while True:
        p = next(q for q in c.get(f"/api/boards/{board}").json()["panels"] if q["id"] == pid)
        if p["render"]["status"] != "rendering" or time.time() - t > timeout:
            return p
        time.sleep(0.3)


def log(step: str, **kw) -> None:
    print(f"== {step}: " + json.dumps(kw, default=str)[:1500], flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile")
    ap.add_argument("--llm-config")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    root = out / "project"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("build", "scratch", "pintu_out", ".pintu", "__pycache__"))
    # Stage 1: a scratch output whose sidecar names its script, as pintu_sdk.save(fig, path) writes it.
    (root / (ITEM + ".meta.json")).write_text(json.dumps(
        {"recipe": None, "params": {}, "width_mm": 89, "height_mm": 55, "script": "scripts/make_plots.py"}))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "demo")
    report: dict = {}
    project = Project.open(root)
    app = create_app(project, watch=False, llm_factory=default_llm(args.profile, args.llm_config))
    with TestClient(app) as c:
        items = c.get("/api/gallery", params={"q": "lines"}).json()["items"]
        src = c.get("/api/promote/source", params={"path": ITEM}).json()
        log("gallery item", items=[i["path"] for i in items], source=src)
        assert any(i["path"] == ITEM for i in items), "the item is not in the gallery"

        # Stage 1 -> 2: Promote to recipe.
        s = c.post("/api/sessions", json={"kind": "promote", "path": ITEM}).json()
        got = wait_idle(c, s["id"])
        promote = got["session"]
        recipe = (promote["result"] or {}).get("recipe")
        tools = [p["tool"] for p in got["parts"] if p["type"] == "tool"]
        log("promote", id=promote["id"], result=promote["result"], error=promote["error"], steps=promote["steps"],
            tools=tools)
        report["promote"] = {"id": promote["id"], "result": promote["result"], "steps": promote["steps"],
                             "tools": tools, "diff": c.get(f"/api/sessions/{promote['id']}/diff").json()["diff"]}
        assert recipe, "the promote session named no existing recipe"
        r = c.post(f"/api/sessions/{promote['id']}/accept", json={}).json()
        log("accept promote", accepted=r["accepted"])
        recipe_file = root / (recipe.partition(":")[0].replace(".", "/") + ".py")
        promoted_text = recipe_file.read_text()

        # Stage 2 -> 3: place it on a board.
        c.post("/api/boards", json={"name": "fig", "height": 120})
        b = c.post("/api/boards/fig/ops", json={"ops": [{"op": "add", "id": "lines", "cell": [0, 0, 12, 12],
                                                           "recipe": recipe}]}).json()
        p = wait_render(c, "fig", "lines")
        log("placed", cell=p["cell"], render=p["render"], sizeRange=p.get("sizeRange"))
        report["placed"] = {"render": p["render"], "sizeRange": p.get("sizeRange")}
        assert p["render"]["status"] == "ok", "the promoted recipe does not render on the board"
        rng = p.get("sizeRange")
        assert rng and rng.get("max"), "the promoted recipe declares no @panel size range"

        # Stage 3 -> 2: resize outside the range; Adapt to size starts.
        r = c.post("/api/boards/fig/ops", json={"ops": [{"op": "set_cell", "id": "lines", "cell": [0, 0, 36, 12]}]})
        started = r.json()["adaptSessions"]
        p = next(q for q in r.json()["panels"] if q["id"] == "lines")
        log("resized", cell=p["cell"], sizeRange=p["sizeRange"], adaptSessions=started)
        if not started:  # the declared range covers full width: go below it instead
            r = c.post("/api/boards/fig/ops", json={"ops": [{"op": "set_cell", "id": "lines", "cell": [0, 0, 3, 3]}]})
            started = r.json()["adaptSessions"]
            log("resized small", adaptSessions=started, sizeRange=next(
                q for q in r.json()["panels"] if q["id"] == "lines")["sizeRange"])
        assert started, "no Adapt to size session started"
        got = wait_idle(c, started[0])
        adapt = got["session"]
        d = c.get(f"/api/sessions/{adapt['id']}/diff").json()
        log("adapt", id=adapt["id"], result=adapt["result"], error=adapt["error"], steps=adapt["steps"],
            files=[f["path"] for f in d["files"]])
        report["adapt"] = {"id": adapt["id"], "result": adapt["result"], "steps": adapt["steps"], "diff": d["diff"]}
        cell = c.get("/api/boards/fig").json()["panels"][0]["cell"]

        # Revert the adapt session; the promote session's recipe and the user's layout stay.
        rv = c.post(f"/api/sessions/{adapt['id']}/revert", json={})
        log("revert adapt", status=rv.status_code, body=rv.json())
        report["revert"] = rv.json()
        time.sleep(1)
        view = c.get("/api/boards/fig").json()
        p = wait_render(c, "fig", "lines")
        checks = {
            "promote_recipe_exists": recipe_file.is_file(),
            "adapt_changed_files": bool(d["files"]),
            "revert_ok": rv.status_code == 200,
            "recipe_back_to_promoted": recipe_file.read_text() == promoted_text,
            "layout_kept": view["panels"][0]["cell"] == cell,
            "board_renders": p["render"]["status"] == "ok",
            "script_untouched": git(root, "diff", "--", "scripts/make_plots.py") == "",
            "git_head_unchanged": len(git(root, "rev-list", "--all", "--branches").split()) == 1,
            "checkpoint_refs_dropped": "refs/pintu" not in git(root, "for-each-ref"),
            "sessions_persisted": sorted(f.stem for f in (root / ".pintu/sessions").glob("ses_*.json"))
            == sorted([promote["id"], adapt["id"]]),
        }
    report["checks"] = checks
    report["pass"] = all(checks.values())
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    log("checks", **checks)
    print(("PASS" if report["pass"] else "FAIL") + f": {out / 'report.json'}")


if __name__ == "__main__":
    main()
