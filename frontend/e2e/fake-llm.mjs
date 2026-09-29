// A scripted OpenAI-compatible endpoint for the chat e2e specs: POST /v1/chat/completions.
// The reply depends on the turn's task and on how many model calls the turn has made:
//   Promote a scratch plot ...   write_file (a @panel recipe), render_recipe, "RECIPE: ..."
//   Adapt panel ...              edit_file (widen the @panel range), then a summary
//   replace "A" with "B"         read_file, edit_file, render_panel, then a summary
//   FAKE slow                    answers after 120 s (for Stop)
//   FAKE flaky                   503 on the first two calls (for the retry status), then a summary
// Usage: node fake-llm.mjs <port> <project dir>
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { join } from "node:path";

const [port, project] = process.argv.slice(2);
const DELAY = 600; // ms per reply, so the UI shows the session busy
const flaky = new Map();

const text = (m) => (typeof m.content === "string" ? m.content : (m.content ?? []).map((c) => c.text ?? "").join("\n"));

/** The turn's task and the number of model calls made since it. */
function turn(messages) {
  let i = messages.length - 1;
  while (i >= 0 && !(messages[i].role === "user" && !text(messages[i]).startsWith("["))) i--;
  const t = text(messages[i] ?? { content: "" });
  const task = /## Task\n\n([\s\S]*?)(\n\n## |$)/.exec(t)?.[1] ?? t;
  // The recipe context comes with a session's first prompt only.
  const context = messages.filter((m) => m.role === "user").map(text).reverse().join("\n");
  return { task, context, k:messages.slice(i + 1).filter((m) => m.role === "assistant").length };
}

let n = 0;
const call = (name, args) => ({ id: `call_${++n}`, type: "function", function: { name, arguments: JSON.stringify(args) } });
const reply = (content, calls = [], reasoning) => ({ content, calls, reasoning });

const RECIPE = (fn) => `"""Promoted from scripts/make_plots.py."""

import matplotlib.pyplot as plt
import numpy as np
import pintu_sdk


@pintu_sdk.panel(min_size=(40, 30), max_size=(100, 70))
def ${fn}(w, h):
    """Three damped sines."""
    fig = plt.figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_axes((12 / w, 9 / h, 1 - 15 / w, 1 - 13 / h))
    x = np.linspace(0, 10, 200)
    for k in range(3):
        ax.plot(x, np.sin(x + k) * np.exp(-x / (5 + 3 * k)), lw=0.8, label=f"series {k + 1}")
    ax.set(xlabel="time", ylabel="signal")
    return fig
`;

function script({ task, context, k }) {
  if (task.startsWith("Promote a scratch plot")) {
    const module = /Write a new module (\S+) with/.exec(task)[1];
    const fn = /`def (\w+)\(/.exec(task)[1];
    const recipe = /RECIPE: ([^`]+)`/.exec(task)[1];
    return [
      reply("", [call("write_file", { path: module, content: RECIPE(fn) })], "The script draws three damped sines; write a recipe."),
      reply("", [call("render_recipe", { recipe, w: 89, h: 55 })]),
      reply(`Wrote ${module}.\nRECIPE: ${recipe}`),
    ][k] ?? reply("done");
  }
  const path = /## Recipe source \(([^)]+)\)/.exec(context)?.[1];
  if (task.startsWith("Adapt panel")) {
    const line = readFileSync(join(project, path), "utf8").split("\n").find((l) => l.includes("max_size="));
    if (k === 0 && line)
      return reply("", [call("edit_file", { path, old_string: line, new_string: line.replace(/min_size=\([^)]*\)/, "min_size=(10, 10)").replace(/max_size=\([^)]*\)/, "max_size=(400, 300)") })],
        "The new size is outside the range: widen it.");
    return reply("Widened the size range to cover the new size.");
  }
  const rep = /replace "([^"]+)" with "([^"]+)"/.exec(task);
  if (rep && path) {
    const panel = /panel '([^']+)'/.exec(context)?.[1];
    return [
      reply("", [call("read_file", { path })], `Find "${rep[1]}" in the recipe.`),
      reply("", [call("edit_file", { path, old_string: rep[1], new_string: rep[2] })]),
      reply("", [call("render_panel", { id: panel })]),
      reply(`Replaced "${rep[1]}" with "${rep[2]}".`),
    ][k] ?? reply("done");
  }
  return reply("Nothing to do.");
}

createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    if (!req.url.endsWith("/chat/completions")) return res.writeHead(404).end();
    const t = turn(JSON.parse(body).messages);
    // Two failures: the client's own retry, then pintu's retry status.
    if (t.task.includes("FAKE flaky") && (flaky.get(t.task) ?? 0) < 2) {
      flaky.set(t.task, (flaky.get(t.task) ?? 0) + 1);
      return res.writeHead(503, { "Content-Type": "application/json" }).end(JSON.stringify({ error: { message: "warming up" } }));
    }
    const r = t.task.includes("FAKE slow") ? reply("Slow reply.") : script(t);
    const timer = setTimeout(() => {
      const message = { role: "assistant", content: r.content || null, reasoning_content: r.reasoning ?? null };
      if (r.calls.length) message.tool_calls = r.calls;
      res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({
        id: `fake-${n}`, object: "chat.completion", created: Math.floor(Date.now() / 1000), model: "fake",
        choices: [{ index: 0, message, finish_reason: r.calls.length ? "tool_calls" : "stop" }],
        usage: { prompt_tokens: 100, completion_tokens: 10, total_tokens: 110 },
      }));
    }, t.task.includes("FAKE slow") ? 120_000 : DELAY);
    res.on("close", () => clearTimeout(timer));
  });
}).listen(Number(port), "127.0.0.1", () => console.log(`fake LLM on ${port}`));
