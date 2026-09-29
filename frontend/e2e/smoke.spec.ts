import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { PROJECT } from "../playwright.config";

const boardFile = () => readFileSync(join(PROJECT, "boards/demo.board.yaml"), "utf8");

async function previewRev(page: Page) {
  return Number(await page.getByTestId("preview-meta").getAttribute("data-rev"));
}

async function box(page: Page, sel: string) {
  const b = await page.locator(sel).boundingBox();
  if (!b) throw new Error(`no box for ${sel}`);
  return b;
}

test("edit the demo board", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("[data-panel]")).toHaveCount(4);
  await expect(page.getByTestId("preview")).toBeVisible();
  const rev0 = await previewRev(page);

  // Resize bars from the east edge: no API call while dragging; one commit on release.
  const e = await box(page, '[data-panel="bars"] [data-handle="e"]');
  const calls: string[] = [];
  page.on("request", (r) => r.url().includes("/api/") && calls.push(r.url()));
  await page.mouse.move(e.x + e.width / 2, e.y + e.height / 2);
  await page.mouse.down();
  for (let i = 1; i <= 20; i++) await page.mouse.move(e.x + e.width / 2 - i * 8, e.y + e.height / 2);
  expect(calls.filter((u) => !u.includes("/thumb"))).toEqual([]);
  await page.mouse.up();
  await expect.poll(() => previewRev(page)).toBeGreaterThan(rev0);
  expect(boardFile()).not.toContain("cell: [0, 18, 12, 36]");
  await expect(page.getByTestId("preview-meta")).toContainText("edit→preview");

  // Select shows the inspector.
  await page.locator('[data-panel="heatmap"] .body').click();
  await expect(page.getByTestId("inspector")).toContainText("heatmap");

  // Drop a file from the browser onto the freed cells.
  await page.getByText("plots/", { exact: true }).click();
  const src = await box(page, "li.file >> text=lines.pdf");
  const c = await box(page, '[data-panel="heatmap"] .body');
  await page.mouse.move(src.x + 10, src.y + src.height / 2);
  await page.mouse.down();
  await page.mouse.move(src.x + 30, src.y + 20, { steps: 5 });
  await page.mouse.move(c.x - 15, c.y + c.height / 2, { steps: 10 });
  await page.mouse.up();
  await expect(page.locator("[data-panel]")).toHaveCount(5);
  expect(boardFile()).toMatch(/id: lines-2\n\s+cell: \[\d+, \d+, \d+, \d+\]\n\s+source: \{file: plots\/lines.pdf\}/);

  // Split heatmap into 2 columns.
  await page.locator('[data-panel="heatmap"] .body').click();
  await page.getByRole("button", { name: "Split" }).click();
  await expect(page.locator("[data-panel]")).toHaveCount(6);
});

test("preview latency after layout edits", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("preview")).toBeVisible();
  const times: number[] = [];
  for (let i = 0; i < 10; i++) {
    const rev = await previewRev(page);
    const s = await box(page, '[data-panel="scatter"] [data-handle="s"]');
    await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2);
    await page.mouse.down();
    await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2 + (i % 2 ? 12 : -12), { steps: 3 });
    const t0 = await page.evaluate(() => performance.now());
    await page.mouse.up();
    await page.waitForFunction((r) => Number(document.querySelector('[data-testid="preview-meta"]')?.getAttribute("data-rev")) > r, rev);
    times.push((await page.evaluate(() => performance.now())) - t0);
  }
  times.sort((a, b) => a - b);
  console.log(`edit→preview ms: median ${times[5].toFixed(0)}, max ${times[9].toFixed(0)}, all ${times.map((t) => t.toFixed(0)).join(" ")}`);
  expect(times[5]).toBeLessThan(300);
});
