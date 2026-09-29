import { cpSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig } from "@playwright/test";

// Each run serves a fresh copy of the example project.
export const PROJECT = process.env.PINTU_E2E_DIR ?? join(tmpdir(), "pintu-e2e-demo");
const PORT = Number(process.env.PINTU_E2E_PORT ?? 8799);
const LLM_PORT = PORT + 1;
/** Real model run: `PINTU_E2E_PROFILE=glm PINTU_E2E_LLM_CONFIG=… npx playwright test e2e/exit.spec.ts`. */
export const REAL = process.env.PINTU_E2E_PROFILE;
const LLM_CONFIG = `${PROJECT}-llm.toml`;
if (!process.env.PINTU_E2E_COPIED) {
  rmSync(PROJECT, { recursive: true, force: true });
  cpSync(resolve(import.meta.dirname, "../examples/demo"), PROJECT, { recursive: true });
  // Stage 1: a scratch output whose sidecar names its script, as pintu_sdk.save(fig, path) writes it.
  writeFileSync(join(PROJECT, "plots/lines.pdf.meta.json"),
    JSON.stringify({ recipe: null, params: {}, width_mm: 89, height_mm: 55, script: "scripts/make_plots.py" }));
  writeFileSync(LLM_CONFIG, `[profiles.fake]\nbase_url = "http://127.0.0.1:${LLM_PORT}/v1"\nmodel = "fake"\nvision = false\ntools = true\n`);
  process.env.PINTU_E2E_COPIED = "1";
}
const llm = REAL ? `--profile ${REAL} --llm-config ${process.env.PINTU_E2E_LLM_CONFIG}` : `--profile fake --llm-config ${LLM_CONFIG}`;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1600, height: 1000 }, screenshot: "only-on-failure" },
  // The agent specs render and write recipes; they run after the timing-sensitive ones.
  projects: [
    { name: "core", testIgnore: /(chat|exit)\.spec\.ts/ },
    { name: "agent", testMatch: /(chat|exit)\.spec\.ts/, dependencies: REAL ? [] : ["core"] },
  ],
  webServer: [
    ...(REAL ? [] : [{
      command: `node e2e/fake-llm.mjs ${LLM_PORT} ${PROJECT}`,
      port: LLM_PORT,
      reuseExistingServer: false,
    }]),
    {
      command: `uv run --project ../backend pintu serve --project ${PROJECT} --port ${PORT} ${llm}`,
      url: `http://127.0.0.1:${PORT}/api/project`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
