import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { PROJECT } from "../playwright.config";

const boardFile = () => readFileSync(join(PROJECT, "boards/gallery.board.yaml"), "utf8");
const RECIPE = "recipes.cohort:timeline";

test("browse 300 timelines, filter to 3, drop one as a recipe panel", async ({ page }) => {
  test.setTimeout(180_000);
  if (!existsSync(join(PROJECT, "scratch/timelines/S300.pdf.meta.json")))
    execSync(`uv run --project ${join(import.meta.dirname, "../../backend")} python scripts/make_gallery.py`,
      { cwd: PROJECT, stdio: "inherit" });

  // An own board, since the other specs edit the demo boards in parallel.
  expect((await page.request.post("/api/boards", { data: { name: "gallery" } })).ok()).toBe(true);
  await page.goto("/?board=gallery");
  await expect(page.getByTestId("gallery")).toContainText("items");
  const t0 = await page.evaluate(() => performance.now());
  await page.getByTestId("gallery").locator("select").selectOption(RECIPE);
  await expect(page.getByTestId("gallery")).toContainText("300 items");
  // First screen: every visible thumbnail decoded.
  await expect.poll(() => page.evaluate(() => {
    const imgs = [...document.querySelectorAll<HTMLImageElement>("[data-testid=gallery-item] img")]
      .filter((i) => i.getBoundingClientRect().top < innerHeight);
    return imgs.length > 0 && imgs.every((i) => i.complete && i.naturalWidth > 0);
  }), { intervals: [20] }).toBe(true);
  const firstScreen = (await page.evaluate(() => performance.now())) - t0;
  console.log(`gallery first screen: ${firstScreen.toFixed(0)} ms`);
  expect(await page.getByTestId("gallery-item").count()).toBeLessThan(300); // the rest load lazily

  const patient = page.locator(".facet", { hasText: "patient" }).locator("input");
  await patient.fill("S002, S005, S009");
  await patient.press("Enter");
  await expect(page.getByTestId("gallery-item")).toHaveCount(3);

  const canvas = (await page.getByTestId("canvas").boundingBox())!;
  const src = (await page.getByTestId("gallery-item").first().boundingBox())!;
  await page.mouse.move(src.x + 20, src.y + 20);
  await page.mouse.down();
  await page.mouse.move(src.x + 40, src.y + 40, { steps: 5 });
  await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2, { steps: 10 });
  await page.mouse.up();
  await expect(page.locator("[data-panel]")).toHaveCount(1);
  await expect.poll(boardFile).toMatch(/source: \{recipe: recipes.cohort:timeline, params: \{arm: \w, patient: S002, stage: \w+\}\}/);
  await expect(page.locator('[data-testid="render-badge"]')).toHaveCount(0, { timeout: 60_000 });
});
