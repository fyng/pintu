import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { PROJECT } from "../playwright.config";

const RECIPE = "recipes.cohort:timeline";
const boardFile = (name: string) => readFileSync(join(PROJECT, `boards/${name}.board.yaml`), "utf8");

async function drag(page: Page, from: Locator, to: { x: number; y: number }) {
  const src = (await from.boundingBox())!;
  await page.mouse.move(src.x + src.width / 2, src.y + src.height / 2);
  await page.mouse.down();
  await page.mouse.move(src.x + src.width / 2 + 10, src.y + src.height / 2 + 10, { steps: 5 });
  await page.mouse.move(to.x, to.y, { steps: 10 });
  await page.mouse.up();
}

async function center(l: Locator) {
  const b = (await l.boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

test("B1 exit: browse 300 timelines, drag 3 into one multiples panel and reorder them", async ({ page }) => {
  test.setTimeout(240_000);
  if (!existsSync(join(PROJECT, "scratch/timelines/S300.pdf.meta.json")))
    execSync(`uv run --project ${join(import.meta.dirname, "../../backend")} python scripts/make_gallery.py`,
      { cwd: PROJECT, stdio: "inherit" });
  expect((await page.request.post("/api/boards", { data: { name: "multiples", height: 120 } })).ok()).toBe(true);
  await page.goto("/?board=multiples");

  // Browse the 300 timelines, filter to 3.
  const gallery = page.getByTestId("gallery");
  await expect(gallery).toContainText("items");
  await gallery.locator("select").selectOption(RECIPE);
  await expect(gallery).toContainText("300 items");
  const patient = page.locator(".facet", { hasText: "patient" }).locator("input");
  await patient.fill("S002, S005, S009");
  await patient.press("Enter");
  const items = page.getByTestId("gallery-item");
  await expect(items).toHaveCount(3);

  // The first becomes a recipe panel, named after its patient; make it a multiples panel.
  const canvas = (await page.getByTestId("canvas").boundingBox())!;
  await drag(page, items.nth(0), { x: canvas.x + canvas.width * 0.3, y: canvas.y + canvas.height * 0.3 });
  const panel = page.locator('[data-panel="timeline-S002"]');
  await expect(panel).toHaveCount(1);
  await panel.locator("rect.body").click();
  await page.getByTestId("make-multiples").click();
  await expect.poll(() => boardFile("multiples")).toContain("multiples: {item: patient, mosaic: [[S002]]}");

  // A 1 x 3 grid; the other two go into its empty cells.
  await page.getByTestId("mosaic-cols").fill("3");
  await expect(page.getByTestId("mosaic-cell")).toHaveCount(3);
  const cell = (rc: string) => page.locator(`[data-testid=mosaic-cell][data-cell="${rc}"]`);
  await drag(page, items.nth(1), await center(cell("0,1")));
  await expect(page.locator("[data-testid=mosaic-item][data-item=S005]")).toHaveCount(1);
  await drag(page, items.nth(2), await center(cell("0,2")));
  await expect.poll(() => boardFile("multiples")).toContain("mosaic: [[S002, S005, S009]]");

  // Reorder: S009 onto S002 swaps them.
  await drag(page, page.locator("[data-testid=mosaic-item][data-item=S009]"), await center(cell("0,0")));
  await expect.poll(() => boardFile("multiples")).toContain("mosaic: [[S009, S005, S002]]");
  await expect(page.locator("[data-testid=mosaic-item]")).toHaveText(["S009×", "S005×", "S002×"]);

  // The render follows the mosaic: one SVG with the three patients in the new order.
  await expect(page.locator('[data-testid="render-badge"]')).toHaveCount(0, { timeout: 60_000 });
  await expect.poll(async () => {
    const v = await (await page.request.get("/api/boards/multiples")).json();
    const p = v.panels.find((q: { id: string }) => q.id === "timeline-S002");
    if (p.render.status !== "ok" || !p.file) return null;
    const meta = JSON.parse(readFileSync(join(PROJECT, p.file.replace(/[^/]+$/, "meta.json")), "utf8"));
    const svg = readFileSync(join(PROJECT, p.file), "utf8");
    const at = (id: string) => svg.indexOf(`${id} · stage`);
    return { mosaic: meta.multiples.mosaic, order: at("S009") < at("S005") && at("S005") < at("S002") && at("S009") >= 0 };
  }, { timeout: 60_000 }).toEqual({ mosaic: [["S009", "S005", "S002"]], order: true });
});

test("group two panels under one letter", async ({ page }) => {
  expect((await page.request.post("/api/boards", { data: { name: "groups", height: 120 } })).ok()).toBe(true);
  expect((await page.request.post("/api/boards/groups/ops", { data: { ops: [
    { op: "add", cell: [0, 0, 12, 12], id: "a" }, { op: "add", cell: [12, 0, 24, 12], id: "b" },
    { op: "add", cell: [24, 0, 36, 12], id: "c" },
  ] } })).ok()).toBe(true);
  await page.goto("/?board=groups");
  await page.locator('[data-panel="a"] rect.body').click();
  await page.locator('[data-panel="b"] rect.body').click({ modifiers: ["Shift"] });
  await page.getByTestId("group").click();
  await expect.poll(() => boardFile("groups")).toContain("groups:\n  - {id: group, panels: [a, b]}");
  await expect(page.locator("[data-group=group]")).toHaveCount(1);
  await expect(page.locator(".letter")).toHaveText(["a", "b"]); // a+b share "a"; c is "b"
  await page.locator('[data-panel="b"] rect.body').click();
  await expect(page.getByTestId("group-info")).toContainText("In group group");
  await page.getByRole("button", { name: "Ungroup" }).click();
  await expect.poll(() => boardFile("groups")).not.toContain("groups:");
});
