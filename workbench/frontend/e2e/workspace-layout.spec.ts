import { test, expect } from "@playwright/test";

const prefix = "/api/workbench/v1";

test("preview retains selected run identity across missing media and request failures", async ({
  page,
}) => {
  const summaries = [
    { id: "empty", name: "Failed run without media", validation: "failed" },
    { id: "video", name: "Run with video", validation: "warning" },
    { id: "broken", name: "Unreadable run", validation: "failed" },
  ].map((run) => ({
    ...run,
    updated_at: "2026-10-21T08:00:00Z",
    stage_count: 1,
  }));
  await page.route(`**${prefix}/runs?*`, (route) =>
    route.fulfill({ json: { items: summaries, total: 3 } }),
  );
  await page.route(`**${prefix}/runs/*`, (route) => {
    const id = new URL(route.request().url()).pathname.split("/").pop();
    if (id === "broken")
      return route.fulfill({
        status: 500,
        json: { detail: "预览报告读取失败" },
      });
    return route.fulfill({
      json: {
        ...summaries.find((run) => run.id === id),
        media:
          id === "video"
            ? [{ id: "test-video", kind: "video", name: "test-video.mp4" }]
            : [],
      },
    });
  });
  // A broken media resource must surface the shared player's download fallback.
  await page.route(`**${prefix}/artifacts/test-video/content*`, (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto("/");
  const preview = page.getByRole("region", { name: "最近运行预览" });
  await expect(preview).toContainText("Run with video");
  await expect(preview.getByRole("alert")).toContainText("视频加载或解码失败");
  await expect(
    preview.getByRole("link", { name: "下载原视频" }),
  ).toHaveAttribute("href", /test-video\/content\?download=true$/);
  await page
    .getByRole("button", { name: "预览 Failed run without media", exact: true })
    .click();
  await expect(preview).toContainText("暂无可预览结果");
  await expect(preview).toContainText("失败");
  await expect(
    preview.getByRole("link", { name: "打开工作台" }),
  ).toHaveAttribute("href", "/runs/empty");
  await expect(preview.locator("video")).toHaveCount(0);
  await page
    .getByRole("button", { name: "预览 Unreadable run", exact: true })
    .click();
  await expect(preview.getByRole("alert")).toContainText("预览报告读取失败");
  await expect(
    preview.getByRole("link", { name: "打开工作台" }),
  ).toHaveAttribute("href", "/runs/broken");
  await expect(preview).not.toContainText("Run with video");
});

test("inspection is available on desktop and narrow screens without crowding the default viewer", async ({
  page,
  request,
}) => {
  const runs = await (await request.get(`${prefix}/runs?limit=10`)).json();
  expect(runs.items.length).toBeGreaterThan(0);
  await page.goto(`/runs/${runs.items[0].id}`);
  const toggle = page.getByRole("button", { name: "检查器", exact: true });
  const inspector = page.locator("#run-inspector");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(inspector).toBeHidden();
  await toggle.click();
  await expect(inspector).toBeVisible();
  await inspector.getByRole("button", { name: "查看检查与证据" }).click();
  await expect(
    page.getByRole("tab", { name: "验收", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(inspector).toBeVisible();
  const bounds = await inspector.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await inspector.getByRole("button", { name: "收起", exact: true }).click();
  await expect(inspector).toBeHidden();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});
