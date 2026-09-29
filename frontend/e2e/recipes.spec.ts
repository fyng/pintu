import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { PROJECT } from "../playwright.config";

const href = (page: Page, id: string) => page.locator(`[data-panel="${id}"] image`).getAttribute("href");
const badges = (page: Page) => page.locator('[data-testid="render-badge"]');

async function box(page: Page, sel: string) {
  const b = await page.locator(sel).boundingBox();
  if (!b) throw new Error(`no box for ${sel}`);
  return b;
}

const stats = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return `median ${s[Math.floor(s.length / 2)].toFixed(0)}, max ${s[s.length - 1].toFixed(0)}, all ${xs.map((t) => t.toFixed(0)).join(" ")}`;
};

test("recipe panels re-render on resize and on save", async ({ page }) => {
  await page.goto("/?board=recipes");
  await expect(page.locator("[data-panel]")).toHaveCount(3, { timeout: 20_000 });
  await expect(badges(page)).toHaveCount(0, { timeout: 60_000 });
  for (const id of ["km", "dumbbell", "timeline"]) expect(await href(page, id)).toContain(".svg");

  // Board -> plot: resize the timeline; the old image stays, then the new size swaps in.
  const times: number[] = [];
  for (let i = 0; i < 6; i++) {
    const before = await href(page, "timeline");
    const e = await box(page, '[data-panel="timeline"] [data-handle="e"]');
    await page.mouse.move(e.x + e.width / 2, e.y + e.height / 2);
    await page.mouse.down();
    await page.mouse.move(e.x + e.width / 2 - 40 - i * 12, e.y + e.height / 2, { steps: 4 });
    const t0 = await page.evaluate(() => performance.now());
    await page.mouse.up();
    await page.waitForFunction((b) => {
      const h = document.querySelector('[data-panel="timeline"] image')?.getAttribute("href");
      return h && h !== b && !document.querySelector('[data-panel="timeline"] [data-testid="render-badge"]');
    }, before);
    times.push((await page.evaluate(() => performance.now())) - t0);
  }
  console.log(`resize→new image ms: ${stats(times)}`);
  expect(times.sort((a, b) => a - b)[3]).toBeLessThan(2000);

  // Code -> plot: saving the recipe file re-renders its panel.
  const file = join(PROJECT, "recipes/dumbbell.py");
  const src = readFileSync(file, "utf8");
  const saves: number[] = [];
  for (let i = 0; i < 4; i++) {
    const before = await href(page, "dumbbell");
    const t0 = await page.evaluate(() => performance.now());
    writeFileSync(file, src.replace('label="after"', `label="after ${i}"`));
    await page.waitForFunction((b) => document.querySelector('[data-panel="dumbbell"] image')?.getAttribute("href") !== b, before, { timeout: 10_000 });
    saves.push((await page.evaluate(() => performance.now())) - t0);
  }
  console.log(`save→new image ms: ${stats(saves)}`);
  expect(saves.sort((a, b) => a - b)[2]).toBeLessThan(3000);

  // Code -> board: removing the function keeps the image and shows the badge.
  const kept = await href(page, "dumbbell");
  writeFileSync(file, src.replace("def dumbbell(", "def dumbbell_renamed("));
  await expect(page.locator('[data-panel="dumbbell"] [data-testid="render-badge"]')).toHaveAttribute("data-status", "missing");
  expect(await href(page, "dumbbell")).toBe(kept);
  writeFileSync(file, src);
  await expect(page.locator('[data-panel="dumbbell"] [data-testid="render-badge"]')).toHaveCount(0, { timeout: 30_000 });

  // Plot -> code: the built-in view opens at the def.
  await page.locator('[data-panel="km"] .body').click();
  await expect(page.getByTestId("recipe-input")).toHaveValue("recipes.km:km");
  await page.getByRole("button", { name: "Open code" }).click();
  await expect(page.getByTestId("code-view")).toContainText("recipes/km.py:");
  await expect(page.locator(".cm-def-line")).toContainText("def km(");
  await page.getByRole("button", { name: "Close" }).click();

  // Param edit re-renders.
  const km = await href(page, "km");
  await page.getByTestId("params-input").fill('{"groups": 3}');
  await page.getByTestId("recipe-input").click();
  await expect.poll(() => href(page, "km"), { timeout: 30_000 }).not.toBe(km);
  expect(readFileSync(join(PROJECT, "boards/recipes.board.yaml"), "utf8")).toContain("params: {groups: 3}");
});
