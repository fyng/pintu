import asyncio
import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from pintu import agent as ag
from pintu.board import Board
from pintu.llm import LLM, Profile, load_profile, parse_text_calls
from pintu.project import Project
from pintu.recipes import SubprocessRunner
from pintu.renders import Renders

FIXTURE = Path(__file__).parent / "fixtures" / "agent"
RECIPE = "recipes/dist.py"


class FakeClient:
    """Scripted stand-in for openai.AsyncOpenAI; records each request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw)))
        msg = self.replies.pop(0) if self.replies else self.replies_last
        self.replies_last = msg
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


def native(content="", calls=(), reasoning=None):
    tcs = [SimpleNamespace(id=f"c{k}", function=SimpleNamespace(name=n, arguments=json.dumps(a)))
           for k, (n, a) in enumerate(calls)]
    return SimpleNamespace(content=content, tool_calls=tcs or None, reasoning_content=reasoning)


def text(content, reasoning=None):
    return SimpleNamespace(content=content, tool_calls=None, reasoning_content=reasoning)


@pytest.fixture
def project(tmp_path):
    dst = tmp_path / "proj"
    shutil.copytree(FIXTURE, dst)
    return Project.open(dst)


def make(project, replies, tools=True, vision=False, **kw):
    client = FakeClient(replies)
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=tools, vision=vision), client=client)
    a = ag.Agent(project, "eval", "boxes", Renders(project, SubprocessRunner(project), ag._no_board), llm, **kw)
    return a, client


def test_loop_native(project):
    old = 'ax.set_ylabel("Response (a.u.)")'
    new = 'ax.set_ylabel("Response")'
    a, client = make(project, [
        native(calls=[("read_file", {"path": RECIPE})], reasoning="let me look"),
        native(calls=[("edit_file", {"path": RECIPE, "old_string": old, "new_string": new}),
                      ("render_panel", {"id": "boxes"})]),
        native("Renamed the axis label."),
    ])
    res = asyncio.run(a.run("rename the y label"))
    assert res.stopped == "done" and res.steps == 3 and res.text == "Renamed the axis label."
    assert res.tool_calls == ["read_file", "edit_file", "render_panel"]
    assert res.usage == {"prompt": 30, "completion": 15, "total": 45}
    assert new in (project.root / RECIPE).read_text()
    assert all("tools" in r for r in client.requests)
    last = client.requests[-1]["messages"]
    tool_msgs = [m for m in last if m["role"] == "tool"]
    assert len(tool_msgs) == 3 and tool_msgs[0]["tool_call_id"] == "c0"
    assert "def boxes" in tool_msgs[0]["content"] and "ok: edited" in tool_msgs[1]["content"]
    assert tool_msgs[2]["content"].startswith("render ok at 90 x 46.361 mm")
    assert not any("reasoning" in json.dumps(m) for m in last if m["role"] == "assistant")
    recs = [json.loads(ln) for ln in res.transcript.read_text().splitlines()]
    assert res.transcript.parent == project.root / ".pintu" / "agent"
    kinds = [r["type"] for r in recs]
    assert kinds[0] == "start" and kinds[-1] == "end" and kinds.count("reply") == 3 and kinds.count("tool") == 3
    reply = next(r for r in recs if r["type"] == "reply")
    assert reply["reasoning"] == "let me look" and reply["usage"]["total"] == 15 and "seconds" in reply


def test_kernel_path(project):
    """The CLI's render path: cache, then the recipe kernel; an edit re-renders."""
    llm = LLM(Profile("fake", "http://x/v1", "m", tools=True), client=FakeClient([native("ok")]))
    renders = ag.local_renders(project)
    a = ag.Agent(project, "eval", "boxes", renders, llm)

    async def go():
        try:
            first, _, _ = await a.render("boxes")
            again, _, _ = await a.render("boxes")
            a.t_edit_file(RECIPE, 'ax.set_ylabel("Response (a.u.)")', 'ax.set_ylabel("Response")')
            edited, _, _ = await a.render("boxes")
            return first, again, edited
        finally:
            await renders.stop()

    first, again, edited = asyncio.run(go())
    assert first.ok and not first.cached and again.cached and again.svg == first.svg
    assert edited.ok and not edited.cached and edited.code_hash != first.code_hash
    assert (project.root / ".pintu" / "renders.json").is_file()


def test_step_cap(project):
    a, client = make(project, [native(calls=[("list_dir", {"path": ""})])], max_steps=3)
    res = asyncio.run(a.run("loop forever"))
    assert res.stopped == "step_cap" and res.steps == 3 and len(client.requests) == 3


def test_loop_text_mode(project):
    call = '{"name": "grep", "arguments": {"pattern": "def boxes"}}'
    a, client = make(project, [
        text(f"I will search.\n```tool_call_json\n{call}\n```",
             reasoning='<tool_call>{"name": "edit_file", "arguments": {}}</tool_call>'),
        text("Found it."),
    ], tools=False)
    res = asyncio.run(a.run("find the recipe"))
    assert res.stopped == "done" and res.tool_calls == ["grep"]
    req = client.requests[-1]
    assert "tools" not in req
    assert "tool_call_json" in req["messages"][0]["content"] and "render_panel" in req["messages"][0]["content"]
    results = req["messages"][3]
    assert results["role"] == "user" and "recipes/dist.py:" in results["content"][0]["text"]


def test_text_mode_announced_call(project):
    """Task 2 of the text eval: a reply that announces a call but has none must not end the run."""
    a, client = make(project, [
        text("I'll start by reading the style helper.", reasoning="Let me call read_file."),
        native(calls=[("read_file", {"path": RECIPE})]),  # vLLM's parser took the call out of the text
        text('<tool_call>edit_file<arg_key>path</arg_key><arg_value>recipes/dist.py</arg_value>'
             '<arg_key>old_string</arg_key><arg_value>ax.set_ylabel("Response (a.u.)")</arg_value>'
             '<arg_key>new_string</arg_key><arg_value>ax.set_ylabel("Response")</arg_value></tool_call>'),
        text("Renamed the label."),
    ], tools=False)
    res = asyncio.run(a.run("rename the y label"))
    assert res.stopped == "done" and res.steps == 4 and res.text == "Renamed the label."
    assert res.tool_calls == ["read_file", "edit_file"]
    assert 'ax.set_ylabel("Response")' in (project.root / RECIPE).read_text()
    msgs = client.requests[-1]["messages"]
    assert msgs[3] == {"role": "user", "content": ag.NUDGE}
    assert "```tool_call_json" in msgs[4]["content"] and "read_file" in msgs[4]["content"]


def test_parse_text_calls():
    calls = parse_text_calls('<tool_call>{"name": "a", "arguments": {"x": 1}}</tool_call>'
                             '<tool_call>{"tool": "b", "args": "{\\"y\\": 2}"}</tool_call>')
    assert [(c.name, c.arguments) for c in calls] == [("a", {"x": 1}), ("b", {"y": 2})]
    fenced = 'Sure.\n```json\n{"tool_calls": [{"function": {"name": "c", "arguments": "{}"}}]}\n```'
    assert [c.name for c in parse_text_calls(fenced)] == ["c"]
    bare = 'Call: [{"name": "d", "parameters": {"id": "p"}}] then done'
    assert [(c.name, c.arguments) for c in parse_text_calls(bare)] == [("d", {"id": "p"})]
    assert parse_text_calls('<think>{"name": "e", "arguments": {}}</think>All done {not json}') == []
    bad = parse_text_calls('<tool_call>{"name": "f", "arguments": "{oops"}</tool_call>')
    assert bad[0].name == "f" and "not valid JSON" in bad[0].error
    unclosed = parse_text_calls('<tool_call>{"name": "g", "arguments": {}}')
    assert [c.name for c in unclosed] == ["g"]


# Trimmed from the P4 text-kernel eval (2-halve-width): the fence misses its final "}".
BROKEN = ('I\'ll rewrite it. ```tool_call_json\n{"name": "edit_file", "arguments": {"path": '
          '"recipes/composition.py", "old_string": "    ax.legend(frameon=False, title=\\"Cell type\\")\\n'
          '    return fig", "new_string": "    if w < 60:\\n        ax.set_xlim(0, 1)  # {not a brace}\\n'
          '    return fig"}\n```')


def test_parse_repairs_missing_closers():
    [c] = parse_text_calls(BROKEN)
    assert c.name == "edit_file" and c.error is None
    assert c.arguments["path"] == "recipes/composition.py" and "{not a brace}" in c.arguments["new_string"]
    [c] = parse_text_calls('```tool_call_json\n{"name": "a", "arguments": {"xs": [1, {"y": "]}"}\n```')
    assert c.arguments == {"xs": [1, {"y": "]}"}]}


def test_parse_unrepairable_call_is_error(project):
    [c] = parse_text_calls('```tool_call_json\n{"name": "edit_file", "arguments": {"path": "a" "b"}}\n```')
    assert c.name == "edit_file" and "not valid JSON" in c.error and "line 1 column 49" in c.error
    assert parse_text_calls("```python\nd = {1: 2\n```") == []
    bad = BROKEN.replace('"path": ', '"path" ')
    a, client = make(project, [text(bad), text("Done.")], tools=False)
    r = asyncio.run(a.run("halve it"))
    assert r.stopped == "done" and r.steps == 2
    sent = client.requests[1]["messages"][-1]["content"]
    assert "Result of edit_file" in str(sent) and "not valid JSON" in str(sent)


def test_inline_think_stripped(project):
    llm = LLM(Profile("fake", "u", "m", tools=False),
              client=FakeClient([text('<think>maybe {"name": "x", "arguments": {}}</think>Done.')]))
    r = asyncio.run(llm.chat([{"role": "user", "content": "hi"}], ag.TOOLS))
    assert r.content == "Done." and r.tool_calls == [] and r.reasoning.startswith("maybe")


def test_path_confinement(project, tmp_path):
    a, _ = make(project, [])
    (tmp_path / "secret.txt").write_text("no")
    os.symlink(tmp_path / "secret.txt", project.root / "link.txt")
    for bad in ("../secret.txt", "/etc/passwd", "link.txt"):
        out, _ = asyncio.run(a.call("read_file", {"path": bad}))
        assert out.startswith("error:"), bad
    out, _ = asyncio.run(a.call("edit_file", {"path": "../secret.txt", "old_string": "no", "new_string": "yes"}))
    assert out.startswith("error:") and (tmp_path / "secret.txt").read_text() == "no"
    (project.root / ".git").mkdir()
    (project.root / ".git" / "config").write_text("a")
    out, _ = asyncio.run(a.call("edit_file", {"path": ".git/config", "old_string": "a", "new_string": "b"}))
    assert "refusing" in out
    assert asyncio.run(a.call("grep", {"pattern": "x", "path": ".."}))[0].startswith("error:")
    assert asyncio.run(a.call("list_dir", {"path": "../"}))[0].startswith("error:")


def test_read_list_grep(project):
    a, _ = make(project, [])
    assert "recipes/" in a.t_list_dir("")
    assert "recipes/dist.py" in a.t_list_dir("recipes")
    assert a.t_read_file("./" + RECIPE).startswith('"""Response')
    hits = a.t_grep(r"def \w+\(w, h")
    assert "recipes/dist.py:" in hits and "recipes/groups.py:" in hits
    assert a.t_grep("zzz_nothing") == "no matches"
    assert asyncio.run(a.call("grep", {"pattern": "("}))[0].startswith("error: bad regex")


def test_edit_file_unique(project):
    a, _ = make(project, [])
    before = (project.root / RECIPE).read_text()
    out, _ = asyncio.run(a.call("edit_file", {"path": RECIPE, "old_string": '"lw": 0.5', "new_string": "x"}))
    assert "matches 3 times" in out and "starting at lines" in out
    out, _ = asyncio.run(a.call("edit_file", {"path": RECIPE, "old_string": "nope", "new_string": "x"}))
    assert "matches 0 times" in out and "Closest region" in out
    out, _ = asyncio.run(a.call("edit_file", {"path": RECIPE, "old_string": "", "new_string": "x"}))
    assert out.startswith("error:")
    assert (project.root / RECIPE).read_text() == before
    out, _ = asyncio.run(a.call("edit_file", {"path": RECIPE, "old_string": "widths=0.5", "new_string": "widths=0.6"}))
    assert out.startswith("ok") and "widths=0.6" in (project.root / RECIPE).read_text()


def test_edit_miss_hint():
    text = "a = 1\ndef f(w, h):\n    x = 2\n    y = 3\n    return x\n"
    out = ag.edit_miss(text, "def f(w, h):\n  x = 2\n  y = 3")
    assert "lines 2-4" in out and "    4|     y = 3" in out and "indentation differs" in out
    assert "different order" in ag.edit_miss(text, "    y = 3\n    x = 2")
    out = ag.edit_miss(text, "    x = 2\n    y = 4")
    assert "First difference at line 4" in out and "'    y = 4'" in out
    big = "\n".join(f"line {i}" for i in range(100))
    out = ag.edit_miss(big, "\n".join(f"line {i}" for i in range(10, 60)) + "x")
    assert "[20 more lines]" in out and len(out) < 3000


def test_set_cell_and_get_board(project):
    a, _ = make(project, [])
    board = json.loads(a.t_get_board())
    assert board["page"]["grid"] == [36, 36]
    boxes = next(p for p in board["panels"] if p["id"] == "boxes")
    assert boxes["cell"] == [0, 22, 18, 33] and boxes["source"]["params"] == {"n": 40}
    out, _ = asyncio.run(a.call("set_cell", {"id": "boxes", "cell": [0, 22, 36, 33]}))
    assert out.startswith("ok") and a.size[0] > 180
    assert Board.load(project.board_path("eval")).panel("boxes")["cell"] == [0, 22, 36, 33]
    for cell, why in (([0, 0, 18, 30], "overlapping"), ([0, 22, 40, 33], "outside"), ([0, 1], "")):
        out, _ = asyncio.run(a.call("set_cell", {"id": "boxes", "cell": cell}))
        assert out.startswith("error: set_cell refused") and why in out
    out, _ = asyncio.run(a.call("set_cell", {"id": "nope", "cell": [0, 34, 2, 36]}))
    assert "no panel" in out
    assert Board.load(project.board_path("eval")).panel("boxes")["cell"] == [0, 22, 36, 33]


def test_unknown_tool_and_bad_args(project):
    a, _ = make(project, [])
    assert "unknown tool" in asyncio.run(a.call("run_python", {"code": "1"}))[0]
    assert "bad arguments" in asyncio.run(a.call("read_file", {"file": "x"}))[0]


def test_context(project):
    a, _ = make(project, [], size=(44, 55), old_size=(89, 55))
    msgs = asyncio.run(a.context("Adapt"))
    system, user = msgs[0]["content"], msgs[1]["content"]
    assert "Style rules (Nature)" in system and "letter band" in system and "tool_call_json" not in system
    body = user[0]["text"]
    assert "Old size: 89 x 55 mm. New size: 44 x 55 mm." in body
    assert "def boxes(w, h, n=40)" in body and 'params {"n": 40}' in body
    assert "render ok at 44 x 55 mm" in body and "lint:" in body and "summary" in body
    assert len(user) == 1  # no image without vision


def test_image_parts(project):
    a, client = make(project, [native(calls=[("render_panel", {"id": "boxes", "w": 60, "h": 40})]),
                               native(calls=[("render_panel", {"id": "boxes"})]),
                               native(calls=[("render_panel", {"id": "trends"})]),
                               native("done")], vision=True)
    asyncio.run(a.run("look"))
    first = client.requests[0]["messages"][1]["content"]
    assert first[1]["type"] == "image_url"
    png = base64.b64decode(first[1]["image_url"]["url"].split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    msgs = client.requests[1]["messages"]
    assert msgs[-2]["role"] == "tool" and "60 x 40 mm" in msgs[-2]["content"]
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"][1]["type"] == "image_url"
    last = client.requests[-1]["messages"]
    images = [p for m in last if isinstance(m.get("content"), list) for p in m["content"] if p["type"] == "image_url"]
    assert len(images) == ag.MAX_IMAGES
    assert "trends" in json.dumps(last[-2])


def test_prune_images():
    img = {"type": "image_url", "image_url": {"url": "data:"}}
    msgs = [{"role": "user", "content": [img, img]}, {"role": "tool", "content": "x"},
            {"role": "user", "content": [{"type": "text", "text": "t"}, img]}]
    out = ag.prune_images(msgs, keep=2)
    kinds = [p["type"] for m in out if isinstance(m["content"], list) for p in m["content"]]
    assert kinds == ["text", "image_url", "text", "image_url"]
    assert msgs[0]["content"] == [img, img]


def test_lint():
    summary = {
        "size_mm": [89.3, 55.0],
        "axes": [{"bbox_mm": [10, 5, 85, 50], "title": "", "xlabel": "", "ylabel": "", "legend": False,
                  "n_xticks": 5, "n_yticks": 5}],
        "texts": [
            {"text": "Label", "fontsize_pt": 7, "bbox_mm": [20, 52, 30, 55.2]},    # ok, within tolerance
            {"text": "Title", "fontsize_pt": 9, "bbox_mm": [1, 1, 30, 4]},         # font; the top-left is free (letter band)
            {"text": "Wide label", "fontsize_pt": 6, "bbox_mm": [80, 20, 95, 23]},  # overflow
            {"text": "10", "fontsize_pt": 6, "bbox_mm": [6, 20, 9, 22]},            # drawn y tick
            {"text": "−5", "fontsize_pt": 6, "bbox_mm": [-2, 51, 1, 53]},          # undrawn x tick
        ],
    }
    problems = ag.lint(summary, 89, 55)
    assert problems[0].startswith("size:")
    assert not any(p.startswith("letter zone") for p in problems)
    assert any("'Title' is 9 pt" in p for p in problems)
    assert [p for p in problems if p.startswith("overflow")] == [
        "overflow: text 'Wide label' at [80, 20, 95, 23] falls outside the figure"]
    assert "−5" not in json.dumps(ag.compact(summary))


def test_load_profile(tmp_path, monkeypatch):
    cfg = tmp_path / "llm.toml"
    cfg.write_text('[profiles.a]\nbase_url = "http://a/v1"\nmodel = "ma"\n\n'
                   '[profiles.b]\nbase_url = "http://b/v1"\nmodel = "mb"\napi_key_env = "K"\n'
                   'vision = true\ntools = false\n')
    assert load_profile(path=cfg).name == "a"
    monkeypatch.setenv("PINTU_LLM_CONFIG", str(cfg))
    monkeypatch.setenv("PINTU_LLM_PROFILE", "b")
    p = load_profile()
    assert (p.model, p.api_key_env, p.vision, p.tools) == ("mb", "K", True, False)
    with pytest.raises(Exception, match="no profile"):
        load_profile("c")


def test_git_dirty(project):
    root = project.root
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=root, check=True)
    assert ag.git_dirty(root) == []
    (root / RECIPE).write_text("x")
    assert ag.git_dirty(root) == [" M " + RECIPE]
