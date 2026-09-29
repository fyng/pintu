import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { REAL } from "../playwright.config";

// B3 exit test: a stage 1 → 3 loop on one figure without leaving pintu. Runs on the scripted
// endpoint by default; with PINTU_E2E_PROFILE (a real model) steps get minutes each.
const STEP = REAL ? 40 * 60_000 : 30_000;
/** A render, not a model step. */
const RENDER = REAL ? 5 * 60_000 : 30_000;

test("B3 exit: gallery item → Promote → Accept → place → resize → Adapt → Accept", async ({ page }, info) => {
  test.setTimeout(REAL ? 120 * 60_000 : 180_000);
  const shots = process.env.PINTU_E2E_SHOTS ?? info.outputPath("shots");
  mkdirSync(shots, { recursive: true });
  let k = 0;
  const t0 = Date.now();
  const mark = async (what: string) => {
    console.log(`[exit] ${((Date.now() - t0) / 1000).toFixed(1)} s  ${what}`);
    await page.screenshot({ path: join(shots, `${String(++k).padStart(2, "0")}-${what.replace(/\W+/g, "-")}.png`) });
  };
  const session = page.getByTestId("session");
  const status = session.getByTestId("session-status").first();

  expect((await page.request.post("/api/boards", { data: { name: "exit" } })).ok()).toBe(true);
  await page.goto("/?board=exit");
  await expect(page.getByTestId("canvas")).toBeVisible();

  // Stage 1: the scratch plot in the gallery; its script is known, so it can be promoted.
  await page.getByTestId("gallery").locator("input[type=search]").fill("lines.pdf");
  await page.locator('[data-testid=gallery-item][data-path="plots/lines.pdf"]').click();
  await expect(page.getByTestId("promote")).toBeVisible();
  await mark("gallery item");
  await page.getByTestId("promote").click();

  // Stage 1 → 2: the promote session writes a recipe.
  await expect(session).toBeVisible();
  await expect(status).toHaveAttribute("data-status", "busy");
  await expect(status).toHaveAttribute("data-status", "idle", { timeout: STEP });
  await mark("promote done");
  await expect(page.getByTestId("patch").locator("[data-testid=diff]").first()).toHaveAttribute("data-path", /^recipes\//);
  await expect(page.getByTestId("place-recipe")).toBeVisible();
  await page.getByTestId("accept-all").click();
  await expect(page.locator('[data-testid=turn][data-state="accepted"]')).not.toHaveCount(0);

  // Stage 2 → 3: place it on the board; it renders.
  await page.getByTestId("place-recipe").click();
  const panel = page.locator("[data-panel]");
  await expect(panel).toHaveCount(1);
  const id = (await panel.getAttribute("data-panel"))!;
  await expect(page.locator(`[data-panel="${id}"] image`)).toHaveAttribute("href", /\.svg/, { timeout: RENDER });
  await expect(page.locator(`[data-panel="${id}"] [data-testid=render-badge]`)).toHaveCount(0, { timeout: RENDER });
  await mark("placed and rendered");

  // Stage 3 → 2: resize past the recipe's size range; Adapt to size starts.
  const resize = async (dx: number) => {
    const e = (await page.locator(`[data-panel="${id}"] [data-handle="e"]`).boundingBox())!;
    await page.mouse.move(e.x + e.width / 2, e.y + e.height / 2);
    await page.mouse.down();
    await page.mouse.move(e.x + e.width / 2 + dx, e.y + e.height / 2, { steps: 8 });
    await page.mouse.up();
  };
  const pageBox = (await page.locator("[data-testid=canvas] rect.page").boundingBox())!;
  const e0 = (await page.locator(`[data-panel="${id}"] [data-handle="e"]`).boundingBox())!;
  await resize(pageBox.x + pageBox.width - e0.x - 2);
  const notice = page.getByTestId("chat-notice");
  if (!(await notice.waitFor({ timeout: 10_000 }).then(() => true, () => false))) {
    // The declared range covers full width: go below it instead.
    await resize(-(pageBox.width - 20));
  }
  await expect(notice).toContainText("Adapt to size started", { timeout: 10_000 });
  await mark("resized, adapt started");
  await page.getByTestId("session-back").click();
  await page.locator('[data-testid=session-row][data-kind="adapt"]').first().click();
  await expect(status).toHaveAttribute("data-status", "idle", { timeout: STEP });
  await mark("adapt done");

  // Accept the adaptation; the panel renders at its new size.
  await expect(page.getByTestId("accept-all")).toBeVisible();
  await page.getByTestId("accept-all").click();
  await expect(page.locator('[data-testid=turn][data-state="accepted"]')).not.toHaveCount(0);
  await expect(page.locator(`[data-panel="${id}"] [data-testid=render-badge]`)).toHaveCount(0, { timeout: STEP });
  if (!REAL) await expect(page.locator(`[data-panel="${id}"] [data-testid=size-badge]`)).toHaveAttribute("data-outside", "false");
  await mark("adapt accepted");
});
