import asyncio
import os
import signal
import sys

import pytest

from pintu.kernel import KernelRunner
from pintu.project import Project
from pintu.recipes import RenderRequest

RECIPE = '''
import os, time
from matplotlib.figure import Figure

LOADS = []

def plot(w, h, label="x"):
    LOADS.append(1)
    print("rendering", label)
    fig = Figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_subplot()
    ax.set_xlabel(label)
    ax.set_title(f"call {len(LOADS)} pid {os.getpid()}")
    return fig

def boom(w, h):
    raise ValueError("no data")

def slow(w, h):
    time.sleep(60)

def die(w, h):
    os._exit(1)
'''


@pytest.fixture
def project(tmp_path):
    (tmp_path / "rec.py").write_text(RECIPE)
    return Project.open(tmp_path, create=True)


def run(coro):
    return asyncio.run(coro)


def test_warm_render_reload_and_error(project, tmp_path):
    async def main():
        r = KernelRunner(project)
        try:
            a = await r.render(RenderRequest("rec:plot", {"label": "t"}, 80, 50))
            assert a.ok, a.error
            assert "rendering t" in a.stdout
            assert (tmp_path / a.svg).read_text().count("call 1") == 1
            b = await r.render(RenderRequest("rec:plot", {}, 90, 50))
            assert b.ok and "call 2" in (tmp_path / b.svg).read_text()  # module state kept: warm
            assert b.summary["size_mm"] == [90.0, 50.0]
            pid = b.summary["axes"][0]["title"].split()[-1]

            (tmp_path / "rec.py").write_text(RECIPE.replace('ax.set_xlabel(label)', 'ax.set_xlabel(label + "!")'))
            c = await r.render(RenderRequest("rec:plot", {}, 90, 50))
            assert c.ok and c.code_hash != b.code_hash
            assert c.summary["axes"][0]["xlabel"] == "x!"
            assert c.summary["axes"][0]["title"] == f"call 1 pid {pid}"  # reloaded in the same kernel

            e = await r.render(RenderRequest("rec:boom", {}, 50, 40))
            assert not e.ok and "ValueError: no data" in e.error
            assert (await r.render(RenderRequest("rec:plot", {}, 50, 40))).ok
        finally:
            await r.close()
    run(main())


def test_timeout_interrupts(project):
    async def main():
        r = KernelRunner(project, timeout=1.5)
        try:
            assert (await r.render(RenderRequest("rec:plot", {}, 50, 40))).ok
            s = await r.render(RenderRequest("rec:slow", {}, 50, 40))
            assert not s.ok and "timed out" in s.error
            r.timeout = 60  # the first render after an interrupt can be slow on a loaded host
            again = await r.render(RenderRequest("rec:plot", {}, 50, 40))
            assert again.ok, again.error
            assert "call 2" in again.summary["axes"][0]["title"]  # same kernel: interrupted, not restarted
        finally:
            await r.close()
    run(main())


def test_restart_after_death(project):
    async def main():
        r = KernelRunner(project)
        try:
            assert (await r.render(RenderRequest("rec:plot", {}, 50, 40))).ok
            d = await r.render(RenderRequest("rec:die", {}, 50, 40))
            assert not d.ok and "died" in d.error
            again = await r.render(RenderRequest("rec:plot", {}, 50, 40))
            assert again.ok and "call 1" in again.summary["axes"][0]["title"]
            # Killed from outside between renders.
            pid = int(again.summary["axes"][0]["title"].split()[-1])
            os.kill(pid, signal.SIGKILL)
            await asyncio.sleep(0.5)
            assert (await r.render(RenderRequest("rec:plot", {}, 50, 40))).ok
        finally:
            await r.close()
    run(main())


def test_missing_ipykernel(project, tmp_path):
    fake = tmp_path / "fakepy"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} -S -I \"$@\"\n")
    fake.chmod(0o755)
    project.settings["kernel"] = {"python": str(fake)}
    res = run(KernelRunner(project).render(RenderRequest("rec:plot", {}, 50, 40)))
    assert not res.ok and "pip install ipykernel" in res.error


def test_reloads_changed_dependencies(project, tmp_path):
    (tmp_path / "leaf.py").write_text("LABEL = 'one'\n")
    (tmp_path / "mid.py").write_text("from leaf import LABEL\n")
    (tmp_path / "rec2.py").write_text(
        "from matplotlib.figure import Figure\nfrom mid import LABEL\n\n"
        "def plot(w, h):\n    fig = Figure(figsize=(w / 25.4, h / 25.4))\n"
        "    fig.add_subplot().set_xlabel(LABEL)\n    return fig\n")

    async def main():
        r = KernelRunner(project)
        try:
            a = await r.render(RenderRequest("rec2:plot", {}, 60, 40))
            assert a.ok, a.error
            assert a.summary["axes"][0]["xlabel"] == "one"
            (tmp_path / "leaf.py").write_text("LABEL = 'two'\n")
            b = await r.render(RenderRequest("rec2:plot", {}, 60, 40))
            assert b.ok, b.error
            assert b.code_hash != a.code_hash
            assert b.summary["axes"][0]["xlabel"] == "two"
        finally:
            await r.close()
    run(main())
