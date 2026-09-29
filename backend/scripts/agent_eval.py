"""P4 exit-test harness: runs the agent on five set tasks and writes a report (SPEC §12.1).

Each task copies ``tests/fixtures/agent`` into a fresh git repo, renders before,
runs the agent in a session (``sessions.SessionManager``: an "Adapt to size" or a
prompt session), renders after, and records the session diff, renders, lint, steps,
tokens, time and automatic checks. Output: ``<out>/report.md``, ``<out>/results.json`` and
one folder per task.

    uv run python scripts/agent_eval.py --profile glm --out ../local/agent-eval/run1
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import re
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from pintu import agent as ag
from pintu.llm import LLM, load_profile
from pintu.project import Project
from pintu.sessions import SessionManager

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "agent"
BOARD = "eval"

TASKS = [
    {"name": "1-double-width", "panel": "trends", "old": (89, 55), "new": (178, 55),
     "expect": "adds columns or facets (one group per axes) when wide"},
    {"name": "2-halve-width", "panel": "composition", "old": (89, 55), "new": (44, 55),
     "expect": "reflows: moves the legend below or stacks, when narrow"},
    {"name": "3-short-height", "panel": "signal", "old": (89, 55), "new": (89, 20),
     "expect": "drops or shrinks non-essential elements (title, legend, rug, annotation) when short"},
    {"name": "4-tall-narrow", "panel": "ranking", "old": (89, 50), "new": (35, 100),
     "expect": "switches to horizontal bars when tall and narrow"},
    {"name": "5-label-median", "panel": "boxes", "old": (89, 55), "new": (89, 55),
     "prompt": "Label the median line of each box with its value.",
     "expect": "adds a small median value label per box, inside the figure"},
]

RULE = re.compile(r"\b(w|h|width|height|aspect\w*|ratio\w*)\b\s*[<>]=?|[<>]=?\s*\b(w|h|width|height)\b")
HARDCODE = re.compile(r"\b(w|h)\s*=\s*[\d.]+\s*($|#)|figsize\s*=\s*\(\s*[\d.]+")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=eval@pintu", "-c", "user.name=pintu-eval", *args],
                          cwd=root, capture_output=True, text=True, check=True).stdout


def added_lines(diff: str) -> list[str]:
    return [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]


async def render_png(a: ag.Agent, size, dst: Path) -> dict:
    """Renders the task panel at a size, saves the PNG, returns ok, lint and overflow."""
    res, w, h = await a.render(a.panel_id, *size)
    if not res.ok:
        return {"ok": False, "error": (res.error or "")[-1500:]}
    dst.write_bytes(ag.rasterize(a.project.root, res.svg))
    return {"ok": True, "png": dst.name, "lint": ag.lint(res.summary, w, h), "overflow": ag.overflow(res.summary)}


async def run_task(task: dict, profile, out: Path, max_steps: int) -> dict:
    d = out / task["name"]
    root = d / "project"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("pintu_out", ".pintu", "__pycache__"))
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "fixture")
    project = Project.open(root)
    renders = ag.local_renders(project)
    rec = {"task": task["name"], "panel": task["panel"], "old": task["old"], "new": task["new"],
           "expect": task["expect"]}
    llm = LLM(profile)
    a = ag.Agent(project, BOARD, task["panel"], renders, llm)  # before and after renders only
    mgr = SessionManager(project, renders, lambda: llm, max_steps=max_steps,
                         on_record=lambda r: r["type"] == "reply" and print(
                             f"[{task['name']}] step {r['step']}: {r['seconds']:.0f} s -> "
                             + (", ".join(c["name"] for c in r["tool_calls"]) or "done"), flush=True))
    rec["before"] = {"new": await render_png(a, task["new"], d / "before_new.png"),
                     "old": await render_png(a, task["old"], d / "before_old.png")}
    t0 = time.perf_counter()
    try:
        if task.get("prompt"):
            s = await mgr.create("prompt", BOARD, task["panel"], task["prompt"], size=list(task["new"]))
        else:
            s = await mgr.create("adapt", BOARD, task["panel"], size=list(task["new"]), old_size=list(task["old"]))
        rec["prompt"] = mgr.get(s["id"])["parts"][0]["text"]
        s = await mgr.wait(s["id"])
        res = s["result"] or {}
        calls = [p["tool"] for p in mgr.get(s["id"])["parts"] if p["type"] == "tool"]
        rec.update(session=s["id"], stopped=res.get("stopped"), steps=s["steps"], usage=s["usage"],
                   seconds=res.get("seconds", round(time.perf_counter() - t0, 1)), tool_calls=calls,
                   final_text=res.get("text") or s.get("error") or "")
        if s.get("error"):
            rec["error"] = s["error"]
        transcript = project.root / s["transcript"]
        if transcript.exists():
            shutil.copy(transcript, d / "transcript.jsonl")
        files = mgr.diff(s["id"])["files"]
        diff = "".join(f["diff"] for f in files)
    except Exception:
        rec.update(stopped="error", error=traceback.format_exc()[-3000:], seconds=round(time.perf_counter() - t0, 1),
                   steps=0, prompt=rec.get("prompt", ""))
        diff, files = "", []
    rec["after"] = {"new": await render_png(a, task["new"], d / "after_new.png"),
                    "old": await render_png(a, task["old"], d / "after_old.png")}
    await renders.stop()
    rec["git_files_match"] = sorted(git(root, "diff", "--name-only").split()) == sorted(f["path"] for f in files)
    (d / "diff.patch").write_text(diff)
    rec["diff"] = diff
    added = added_lines(diff)
    new_ok, old_ok = rec["after"]["new"]["ok"], rec["after"]["old"]["ok"]
    rec["checks"] = {
        "changed": bool(diff.strip()),
        "renders_ok": new_ok,
        "no_overflow": new_ok and not rec["after"]["new"]["overflow"],
        "accepts_wh": new_ok and old_ok,
        "size_rule": any(RULE.search(ln) for ln in added) if "prompt" not in task else None,
        "no_hardcoded_size": not any(HARDCODE.search(ln) for ln in added),
    }
    rec["auto_pass"] = all(v for v in rec["checks"].values() if v is not None)
    return rec


def _lint_md(r: dict) -> str:
    if not r["ok"]:
        return "render failed:\n\n```\n" + r["error"] + "\n```"
    return "clean" if not r["lint"] else "\n".join(f"- {p}" for p in r["lint"])


def write_report(out: Path, profile, results: list[dict]) -> None:
    lines = [f"# Agent eval: {profile.model} ({profile.name}), tools={profile.tools}, vision={profile.vision}", "",
             "| Task | Auto | Stopped | Steps | Tokens | Time (s) | Checks |", "|---|---|---|---|---|---|---|"]
    for r in results:
        checks = ", ".join(f"{k}={'-' if v is None else ('ok' if v else 'FAIL')}" for k, v in r["checks"].items())
        lines.append(f"| {r['task']} | {'pass' if r['auto_pass'] else 'fail'} | {r['stopped']} | {r['steps']} | "
                     f"{r.get('usage', {}).get('total', '-')} | {r['seconds']} | {checks} |")
    lines += ["", f"Automatic passes: {sum(r['auto_pass'] for r in results)} of {len(results)}. "
              "Human acceptance: judge each diff and the after renders below.", ""]
    for r in results:
        t = r["task"]
        lines += [f"## {t}", "", f"Panel `{r['panel']}`, {r['old'][0]} x {r['old'][1]} mm -> "
                  f"{r['new'][0]} x {r['new'][1]} mm. Expected: {r['expect']}.", "",
                  f"Prompt: {r['prompt']}", "",
                  "| | at the new size | at the old size |", "|---|---|---|"]
        for when in ("before", "after"):
            cells = [f"![]({t}/{r[when][k]['png']})" if r[when][k]["ok"] else "render failed" for k in ("new", "old")]
            lines.append(f"| {when} | {cells[0]} | {cells[1]} |")
        lines += ["", "Lint after, at the new size:", "", _lint_md(r["after"]["new"]), "",
                  f"Tools called: {', '.join(r.get('tool_calls', [])) or '-'}", "",
                  "Final message:", "", "> " + (r.get("final_text") or r.get("error", "")).replace("\n", "\n> "), "",
                  "```diff", r["diff"].rstrip() or "(no changes)", "```", ""]
    (out / "report.md").write_text("\n".join(lines))
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile")
    ap.add_argument("--llm-config")
    ap.add_argument("--tools", choices=["profile", "on", "off"], default="profile",
                    help="override the profile's native tool calling")
    ap.add_argument("--tasks", help="comma-separated task numbers (default: all)")
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--max-steps", type=int, default=ag.MAX_STEPS)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    profile = load_profile(args.profile, args.llm_config)
    if args.tools != "profile":
        profile = dataclasses.replace(profile, tools=args.tools == "on")
    tasks = TASKS if not args.tasks else [TASKS[int(i) - 1] for i in args.tasks.split(",")]
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.parallel)

    async def one(t):
        async with sem:
            return await run_task(t, profile, out, args.max_steps)

    results = await asyncio.gather(*(one(t) for t in tasks))
    write_report(out, profile, results)
    for r in results:
        print(f"{r['task']}: {'pass' if r['auto_pass'] else 'fail'} {r['checks']} "
              f"steps={r['steps']} s={r['seconds']} files_match_git={r.get('git_files_match')}")
    print(f"report: {out / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
