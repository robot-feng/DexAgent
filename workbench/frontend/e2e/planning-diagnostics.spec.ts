import { expect, test } from "@playwright/test";
import path from "node:path";

const prefix = "/api/workbench/v1";
const runId = "diagnostic-fixture";

test("planning trials expose physical failures without certifying dry runs", async ({
  page,
}) => {
  // Browser-only pending build preview: keeps the shared server and workers intact.
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
      (url) => url.pathname === `/runs/${runId}`,
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
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route(`**${prefix}/runs/${runId}`, (route) =>
    route.fulfill({
      json: {
        id: runId,
        name: "diagnostic fixture",
        source: "dexagent",
        execution: "stopped",
        validation: "failed",
        release: "not_evaluated",
        scope: { requested_stages: [] },
        project_id: "imported",
        updated_at: "2026-10-20T10:00:00Z",
        stage_count: 0,
        issue_count: 0,
        stages: [],
        reports: [],
        issues: [],
        sessions: [],
        media: [],
        available_actions: [],
        recorded_3d_available: false,
        task: "Place cup into bowl",
      },
    }),
  );
  await page.route(`**${prefix}/runs/${runId}/logs`, (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  );
  const artifact = (id: string) => ({
    id,
    name: "execution_report.json",
    path: `runs/example/stage3/.candidates/attempt/${id}/execution_report.json`,
    kind: "text",
    size: 1000,
    modified: "2026-10-20T10:00:00Z",
    privileged: false,
  });
  const physical = {
    report: artifact("physical"),
    sha256: "a".repeat(64),
    outcome: "failed",
    dry_run: false,
    expected_subgoals: 3,
    recorded_subgoals: 3,
    passed_subgoals: 2,
    failed_checks: ["release_settle", "hand_penetration"],
    n_frames: 428,
    duration_s: 14.27,
  };
  const dry = {
    ...physical,
    report: artifact("screen"),
    dry_run: true,
    outcome: "passed",
    sha256: "b".repeat(64),
    passed_subgoals: 3,
    failed_checks: [],
    n_frames: null,
    duration_s: null,
  };
  await page.route(`**${prefix}/runs/${runId}/planning-trials`, (route) =>
    route.fulfill({
      json: {
        items: [physical, dry],
        issues: [
          { path: "bad/execution_report.json", message: "invalid JSON" },
        ],
        truncated: true,
      },
    }),
  );
  await page.route(`**${prefix}/runs/${runId}/planning-trials/*`, (route) => {
    const selected = route.request().url().endsWith("/screen") ? dry : physical;
    return route.fulfill({
      json: {
        ...selected,
        subgoals: [1, 2, 3].map((index) => ({
          index,
          motion: index === 3 ? "place" : "carry",
          side: "left",
          object: "cup",
          ok: selected.dry_run || index < 3,
          failed: index === 3 ? selected.failed_checks : [],
          frames: selected.dry_run ? null : [index * 100, index * 100 + 99],
          measured:
            index === 3 && !selected.dry_run
              ? {
                  max_hand_pen_mm: 3.38,
                  target_rotation_error_deg: 83.078,
                  release_settle: {
                    ok: false,
                    moved_since_release_m: 0.0157,
                    issues: ["object moved after release"],
                    release_frame: 104,
                  },
                }
              : {},
          error: null,
        })),
      },
    });
  });
  await page.goto(`/runs/${runId}?view=planning`);
  await expect(
    page.getByRole("heading", { name: "规划诊断", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("3.38 mm", { exact: true })).toBeVisible();
  await expect(page.getByText("15.7 mm", { exact: true })).toBeVisible();
  await expect(page.getByText("83.078 deg", { exact: true })).toBeVisible();
  await expect(
    page.getByText("object moved after release", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/规划报告目录扫描已截断/)).toBeVisible();
  await expect(page.getByText(/报告读取问题/)).toBeVisible();
  await page.getByLabel("选择规划试验").selectOption("screen");
  await expect(page).toHaveURL(/trial=screen/);
  await expect(
    page.getByText("仅可达性预检 / 未运行物理", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("15.7 mm", { exact: true })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("选择规划试验").selectOption("physical");
  await expect(page.getByText("15.7 mm", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
});
