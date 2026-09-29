import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.PINTU_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../backend/src/pintu/static", emptyOutDir: true },
  server: { proxy: { "/api": { target: backend, ws: true } } },
  test: { include: ["src/**/*.test.ts"] },
});
