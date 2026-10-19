import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  retries: 0,
  timeout: 45_000,
  use: {
    baseURL: process.env.WORKBENCH_URL || "http://127.0.0.1:8765",
    viewport: { width: 1512, height: 982 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  reporter: [["list"]],
});
