import { expect, test } from "@playwright/test";
import path from "node:path";

const prefix = "/api/workbench/v1";

test("native bundle download binds the episode and raw-state choice", async ({
  page,
}) => {
  const dist = process.env.WORKBENCH_PENDING_DIST;
  if (dist) {
    await page.route("**/assets/*", (route) =>
      route.fulfill({
        path: path.join(
          dist,
          "assets",
          path.basename(new URL(route.request().url()).pathname),
        ),
      }),
    );
    await page.route(
      (url) => url.pathname === "/datasets",
      (route) =>
        route.request().isNavigationRequest()
          ? route.fulfill({
              path: path.join(dist, "index.html"),
              contentType: "text/html",
            })
          : route.continue(),
    );
  }
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(`**${prefix}/episode-catalog?*`, (route) =>
    route.fulfill({
      json: {
        total: 1,
        items: [
          {
            id: "episode-a",
            run_id: "run-a",
            name: "0000",
            run_name: "native-fixture",
            project_id: "imported",
            project_name: "已有运行",
            task: "place cup",
            embodiment: "fixture",
            success: null,
            sim_s: 3,
            recorded_steps: 150,
            meta: {
              id: "meta",
              name: "meta.json",
              path: "fixture/meta.json",
              kind: "text",
              size: 100,
              modified: "2026-10-20T00:00:00Z",
              privileged: false,
            },
            steps: null,
            videos: [],
            issues: [],
            missing_fields: [],
          },
        ],
      },
    }),
  );
  let responseKind = "busy";
  let requestBody: unknown;
  await page.route(
    `**${prefix}/runs/run-a/episodes/episode-a/bundle`,
    (route) => {
      requestBody = route.request().postDataJSON();
      return responseKind === "busy"
        ? route.fulfill({
            status: 409,
            json: { detail: "source execution is still running" },
          })
        : route.fulfill({
            json: {
              id: "bundle",
              run_id: "run-a",
              episode_id:
                responseKind === "mismatch" ? "another-episode" : "episode-a",
              includes_simulator_state: (
                requestBody as { include_simulator_state: boolean }
              ).include_simulator_state,
              size: 1024,
              file_count: 4,
              sha256: "a".repeat(64),
              omitted_episode_entries: ["gap/"],
              url: `${prefix}/episode-bundles/fixture/download`,
            },
          });
    },
  );
  // Deliberately a UI contract fixture, not a real native archive.
  await page.route(`**${prefix}/episode-bundles/fixture/download`, (route) =>
    route.fulfill({
      contentType: "application/zip",
      headers: { "content-disposition": 'attachment; filename="fixture.zip"' },
      body: "download contract fixture",
    }),
  );
  await page.goto("/datasets");
  const card = page.locator(".episode-card").filter({ hasText: "place cup" });
  await card.getByText("下载原生记录包", { exact: true }).click();
  const option = card.getByRole("checkbox", {
    name: "包含原始仿真状态与 capture 文件",
  });
  await expect(option).not.toBeChecked();
  await card.getByRole("button", { name: "生成 ZIP", exact: true }).click();
  expect(requestBody).toEqual({ include_simulator_state: false });
  await expect(card.getByRole("alert")).toContainText("still running");
  responseKind = "mismatch";
  await card.getByRole("button", { name: "生成 ZIP", exact: true }).click();
  await expect(card.getByRole("alert")).toContainText("不匹配");
  await expect(
    card.getByRole("link", { name: "下载 ZIP", exact: true }),
  ).toHaveCount(0);
  responseKind = "correct";
  await card.getByRole("button", { name: "生成 ZIP", exact: true }).click();
  const download = page.waitForEvent("download");
  await card.getByRole("link", { name: "下载 ZIP", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("fixture.zip");
  await option.check();
  await expect(
    card.getByRole("link", { name: "下载 ZIP", exact: true }),
  ).toHaveCount(0);
  await card.getByRole("button", { name: "生成 ZIP", exact: true }).click();
  expect(requestBody).toEqual({ include_simulator_state: true });
  await expect(
    card.getByRole("link", { name: "下载 ZIP", exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(option).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
});
