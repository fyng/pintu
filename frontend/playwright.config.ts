import { cpSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { defineConfig } from "@playwright/test";

// Each run serves a fresh copy of the example project.
export const PROJECT = join(tmpdir(), "pintu-e2e-demo");
if (!process.env.PINTU_E2E_COPIED) {
  rmSync(PROJECT, { recursive: true, force: true });
  cpSync(resolve(import.meta.dirname, "../examples/demo"), PROJECT, { recursive: true });
  process.env.PINTU_E2E_COPIED = "1";
}
const PORT = 8799;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1600, height: 1000 } },
  webServer: {
    command: `uv run --project ../backend pintu serve --project ${PROJECT} --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/project`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
