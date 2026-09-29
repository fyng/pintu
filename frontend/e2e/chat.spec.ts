import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { PROJECT, REAL } from "../playwright.config";

// The chat panel against the scripted endpoint in fake-llm.mjs (see its header for the prompts).
test.skip(!!REAL, "scripted-model spec");

const recipe = (name: string, range: string) => `import matplotlib.pyplot as plt
from pintu_sdk import panel


@panel(${range})
def ${name}(w, h):
    fig = plt.figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_axes((12 / w, 9 / h, 1 - 15 / w, 1 - 13 / h))
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.set_xlabel("Time")
    ax.set_ylabel("Signal")
    ax.set_title("Demo")
    return fig
`;
const FIG = join(PROJECT, "recipes/chatfig.py");

test.beforeAll(async ({ request }) => {
  writeFileSync(FIG, recipe("chatfig", "min_size=(20, 15), max_size=(150, 120)"));
  writeFileSync(join(PROJECT, "recipes/chatsmall.py"), recipe("chatsmall", "min_size=(10, 10), max_size=(20, 15)"));
  if ((await request.get("/api/boards/sessions")).ok()) return; // a retried worker
  expect((await request.post("/api/boards", { data: { name: "sessions" } })).ok()).toBe(true);
  const r = await request.post("/api/boards/sessions/ops", { data: { ops: [
    { op: "add", id: "fig", cell: [0, 0, 18, 12], recipe: "recipes.chatfig:chatfig" },
    { op: "add", id: "small", cell: [18, 0, 30, 12], recipe: "recipes.chatsmall:chatsmall" },
  ] } });
  expect(r.ok()).toBe(true);
});

const session = (page: Page) => page.getByTestId("session");
const status = (page: Page) => session(page).getByTestId("session-status").first();

async function selectPanel(page: Page, id: string) {
  await page.locator(`[data-panel="${id}"] .body`).click();
  await expect(page.getByTestId("inspector").locator("h3")).toHaveText(id);
}

async function newPrompt(page: Page, text: string) {
  if (await session(page).isVisible()) await page.getByTestId("session-back").click();
  await page.getByTestId("prompt-input").fill(text);
  await page.getByTestId("prompt-send").click();
  await expect(session(page)).toBeVisible();
}

test("prompt from the inspector: live parts, patch diff, Accept", async ({ page }) => {
  await page.goto("/?board=sessions");
  await selectPanel(page, "fig");
  await page.getByTestId("panel-prompt").click();
  await expect(page.getByTestId("prompt-bind")).toBeChecked();
  await expect(page.getByTestId("prompt-input")).toBeFocused();
  await page.getByTestId("prompt-input").fill('replace "Signal" with "Response"');
  await page.getByTestId("prompt-send").click();

  // Live: busy, then tool parts arrive and finish, then idle with a patch.
  await expect(status(page)).toHaveAttribute("data-status", "busy");
  await expect(page.getByTestId("tool-part").first()).toBeVisible();
  await expect(page.getByTestId("reasoning").first()).not.toHaveAttribute("open", "");
  await expect(status(page)).toHaveAttribute("data-status", "idle", { timeout: 30_000 });
  await expect(page.getByTestId("tool-part")).toHaveCount(3);
  await expect(page.locator('[data-testid=tool-part][data-status="done"]')).toHaveCount(3);
  await page.locator('[data-testid=tool-part][data-tool="edit_file"] summary').click();
  await expect(page.locator('[data-testid=tool-part][data-tool="edit_file"] pre').first()).toContainText('"old_string": "Signal"');
  await expect(page.getByTestId("text-assistant")).toContainText('Replaced "Signal" with "Response"');
  const diff = page.locator('[data-testid=diff][data-path="recipes/chatfig.py"]');
  await expect(diff.locator(".dl-del")).toContainText('ax.set_ylabel("Signal")');
  await expect(diff.locator(".dl-add")).toContainText('ax.set_ylabel("Response")');

  // Trace shows the raw session and parts.
  await page.getByTestId("session-trace").click();
  await expect(page.getByTestId("trace")).toContainText('"type": "patch"');
  await page.getByTestId("session-trace").click();

  await page.getByTestId("turn-accept").click();
  await expect(page.getByTestId("turn")).toHaveAttribute("data-state", "accepted");
  expect(readFileSync(FIG, "utf8")).toContain('ax.set_ylabel("Response")');
  await page.getByTestId("session-back").click();
  await expect(page.getByTestId("session-row").filter({ hasText: 'replace "Signal"' })).toHaveCount(1);
});

test("a second session: staged revert, then a revert conflict kept with skip", async ({ page }) => {
  await page.goto("/?board=sessions");
  await page.getByTestId("tab-chat").click();
  await selectPanel(page, "fig");
  await newPrompt(page, 'replace "Time" with "Time (s)"');
  await expect(status(page)).toHaveAttribute("data-status", "idle", { timeout: 30_000 });
  await page.getByTestId("followup-input").fill('replace "Demo" with "Demo run"');
  await page.getByTestId("followup-send").click();
  await expect(page.getByTestId("turn")).toHaveCount(2);
  await expect(status(page)).toHaveAttribute("data-status", "idle", { timeout: 30_000 });
  await expect(page.locator('[data-testid=turn][data-state="open"]')).toHaveCount(2);
  expect(readFileSync(FIG, "utf8")).toContain('"Demo run"');

  // Staged revert: undo turn 2 only.
  await page.locator('[data-testid=turn][data-turn="2"] [data-testid=turn-revert]').click();
  await expect(page.locator('[data-testid=turn][data-turn="2"]')).toHaveAttribute("data-state", "reverted");
  await expect(page.locator('[data-testid=turn][data-turn="1"]')).toHaveAttribute("data-state", "open");
  expect(readFileSync(FIG, "utf8")).toContain('ax.set_title("Demo")');

  // The user edits the file the agent wrote; reverting turn 1 then conflicts.
  writeFileSync(FIG, readFileSync(FIG, "utf8").replace('"Time (s)"', '"Time (min)"'));
  await page.locator('[data-testid=turn][data-turn="1"] [data-testid=turn-revert]').click();
  await expect(page.getByTestId("conflicts")).toContainText("recipes/chatfig.py (turn 1): changed after the agent's edit");
  await expect(page.getByTestId("revert-overwrite")).toBeVisible();
  await page.getByTestId("revert-skip").click();
  await expect(page.getByTestId("conflicts")).toHaveCount(0);
  await expect(page.locator('[data-testid=turn][data-turn="1"]')).toHaveAttribute("data-state", "reverted");
  await expect(page.locator('[data-testid=turn][data-turn="1"]')).toContainText("kept recipes/chatfig.py");
  expect(readFileSync(FIG, "utf8")).toContain('"Time (min)"');
  await page.getByTestId("session-back").click();
  await expect(page.getByTestId("session-row").filter({ hasText: "sessions/fig" })).toHaveCount(2);
});

test("Stop a slow turn; a transient error shows the retry status", async ({ page }) => {
  await page.goto("/?board=sessions");
  await page.getByTestId("tab-chat").click();
  await newPrompt(page, "FAKE slow");
  await expect(status(page)).toHaveAttribute("data-status", "busy");
  await page.getByTestId("session-stop").click();
  await expect(status(page)).toHaveAttribute("data-status", "idle", { timeout: 15_000 });
  await expect(page.getByTestId("turn")).toContainText("aborted");

  await page.getByTestId("followup-input").fill("FAKE flaky");
  await page.getByTestId("followup-send").click();
  await expect(status(page)).toHaveAttribute("data-status", "retry");
  await expect(status(page)).toContainText(/retry 1 in \d s/);
  await expect(status(page)).toHaveAttribute("data-status", "idle", { timeout: 20_000 });
  await expect(page.getByTestId("text-assistant")).toContainText("Nothing to do.");
});

test("size badge outside the range: Adapt to size, Accept", async ({ page }) => {
  await page.goto("/?board=sessions");
  const badge = page.locator('[data-panel="small"] [data-testid=size-badge]');
  await expect(badge).toHaveAttribute("data-outside", "true");
  await expect(page.locator('[data-panel="fig"] [data-testid=size-badge]')).toHaveAttribute("data-outside", "false");
  await badge.click();
  await expect(session(page)).toBeVisible();
  await expect(session(page).locator(".kind, .meta").first()).toContainText("adapt");
  await expect(status(page)).toHaveAttribute("data-status", "idle", { timeout: 30_000 });
  await expect(page.locator('[data-testid=diff][data-path="recipes/chatsmall.py"] .dl-add')).toContainText("max_size=(400, 300)");
  await page.getByTestId("accept-all").click();
  await expect(page.getByTestId("turn")).toHaveAttribute("data-state", "accepted");
  await expect(badge).toHaveAttribute("data-outside", "false", { timeout: 15_000 });
});
