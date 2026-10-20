import { expect, test } from "@playwright/test";

const prefix = "/api/workbench/v1";
const standard = "e2e_reference_layout_20261016";
const failed = "rss_reference_raw_blocking_final_20261016";

test("background cleanup defaults to native SAM3 LiteText tracking and keeps model comparisons", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/background-cleanup");
  await expect(page.getByRole("heading", { name: "背景清理", exact: true })).toBeVisible();
  const video = page.getByLabel("输入视频");
  await expect(video).toHaveValue(/.+/);
  await expect(video.locator("option:checked")).toContainText("ThrowAwayTrash.mp4");
  const operationDemoNotice = page.getByText("操作演示可用于 mask/补洞流程冒烟", { exact: false });
  await expect(operationDemoNotice).toBeVisible();
  await expect(operationDemoNotice).toContainText("不保证覆盖完整场景几何");
  await expect(operationDemoNotice).not.toContainText("固定机位");
  const proposal = page.getByLabel("对象提案 provider");
  await expect(proposal).toHaveValue("codex_external");
  await expect(proposal.locator("option")).toContainText([
    "DexAgent / Codex external（推荐）",
    "Gemini API / Vertex（上游兼容）",
    "手动对象提示词",
  ]);
  const model = page.getByLabel("视频分割模型");
  await expect(model).toHaveValue("litetext");
  await expect(model.locator("option")).toContainText([
    "SAM3 LiteText 视频跟踪（本机 S0 权重）",
    "SAM3 单模型（文字分割 + 跨帧跟踪）",
    "SimFoundry 原流程（SAM3 mask + SAM2 传播）",
  ]);
  await expect(page.getByRole("button", { name: "生成 mask 预览" })).toBeEnabled();
  await expect(page.getByText("SimFoundry SAM3/SAM2 分割依赖")).toHaveCount(0);
  await model.selectOption("sam3");
  await expect(page.getByRole("button", { name: "生成 mask 预览" })).toBeDisabled();
  await expect(page.getByText("SimFoundry SAM3/SAM2 分割依赖")).toBeVisible();
  await model.selectOption("sam2");
  await expect(page.getByRole("button", { name: "生成 mask 预览" })).toBeDisabled();
  await expect(page.getByText("SAM2.1 Hiera Large 权重")).toBeVisible();
  expect(errors).toEqual([]);
});

test("background cleanup mask review synchronizes the source and mask timelines", async ({ page, request }) => {
  const jobs = await (await request.get(`${prefix}/background-cleanups/jobs`)).json();
  const reviewJob = jobs.find((job: { status: string }) => job.status === "review_mask");
  if (!reviewJob) {
    test.skip(true, "requires a real cleanup task waiting at the mask review gate");
    return;
  }

  await page.goto("/background-cleanup");
  await page.locator(".cleanup-job-row")
    .filter({ hasText: reviewJob.source_video.name })
    .filter({ hasText: "待检查 mask" })
    .first()
    .click();

  const source = page.getByTestId("mask-review-source");
  const mask = page.getByTestId("mask-review-output");
  await expect(page.getByTestId("cleanup-synchronized-review")).toBeVisible();
  await expect(source).toHaveAttribute("src", reviewJob.source_video_url);
  await expect(mask).toHaveAttribute("src", reviewJob.mask_video_url);
  await expect(source).toHaveAttribute("aria-label", "原始视频");
  await expect(mask).toHaveAttribute("aria-label", "对象 mask（黑色为待移除区域）");

  await page.waitForFunction(() => {
    const sourceVideo = document.querySelector('[data-testid="mask-review-source"]') as HTMLVideoElement | null;
    const maskVideo = document.querySelector('[data-testid="mask-review-output"]') as HTMLVideoElement | null;
    return Boolean(sourceVideo && maskVideo && sourceVideo.readyState >= 1 && maskVideo.readyState >= 1);
  });
  await source.evaluate((node) => {
    const video = node as HTMLVideoElement;
    video.currentTime = Math.min(3, video.duration / 2);
    video.dispatchEvent(new Event("seeking"));
  });
  await expect.poll(() => mask.evaluate((node) => (node as HTMLVideoElement).currentTime))
    .toBeGreaterThan(1);
  await mask.evaluate((node) => {
    const video = node as HTMLVideoElement;
    video.currentTime = Math.min(6, video.duration / 2);
    video.dispatchEvent(new Event("seeking"));
  });
  await expect.poll(() => source.evaluate((node) => (node as HTMLVideoElement).currentTime))
    .toBeGreaterThan(4);
});

test("workspace overview links to real runs, scenes, editor sessions and capability status", async ({
  page,
  request,
}) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const health = await (await request.get(`${prefix}/health`)).json();
  const capabilities = await (await request.get(`${prefix}/capabilities`)).json();
  const editors = await (await request.get(`${prefix}/editors`)).json();
  await page.goto("/");
  await expect(page).toHaveTitle("EmbodiForge Studio");
  await expect(
    page.getByRole("link", { name: "EmbodiForge Studio 总览" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "工作空间总览", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "工作台", exact: true })).toHaveCount(0);
  await expect(page.getByText("已索引运行", { exact: true })).toBeVisible();
  await expect(page.locator(".overview-stat").first()).toContainText(
    String(health.run_count),
  );
  await expect(
    page.getByRole("link", { name: /场景与资产/ }).first(),
  ).toBeVisible();
  const activeEditor = editors.find((editor: { status: string }) =>
    ["starting", "ready", "unresponsive"].includes(editor.status),
  );
  if (activeEditor) {
    await expect(
      page.getByRole("link", { name: /继续场景编辑/ }),
    ).toHaveAttribute("href", new RegExp(`/scenes\\?scene=.*editor=${activeEditor.id}`));
  } else {
    await expect(
      page.getByRole("link", { name: /继续场景编辑/ }),
    ).toHaveCount(0);
  }
  const studio = capabilities.find((item: { id: string }) => item.id === "studio");
  await expect(page.locator(".overview-capabilities")).toContainText(
    studio.available ? "可用" : "尚未接入",
  );
  await expect(page.locator(".studio-live-panel")).toContainText("未配置");
  await expect(page.locator(".studio-live-panel")).toContainText(
    "WORKBENCH_RSS_RUN_DIR",
  );
  await page.getByRole("link", { name: /打开项目与运行/ }).click();
  await expect(page).toHaveURL(/\/projects$/);
  await expect(
    page.getByRole("heading", { name: "从一次运行，看到完整过程" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "工作空间总览", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.getByRole("link", { name: /工具库/ }).first().click();
  await expect(page).toHaveURL(/\/tools$/);
  expect(pageErrors).toEqual([]);
});

test("workspace overview shows live RSS state and links to its native viewer", async ({ page }) => {
  await page.route(`**${prefix}/studio/live`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        connected: true,
        checked_at: "2026-10-19T08:00:00+00:00",
        run_dir: "/tmp/rss-run",
        url: "http://localhost:8080",
        step: 42,
        sim_time: 1.25,
        paused: false,
        finite: true,
        playing: 0,
        generation: 1,
        names: ["robot", "cube"],
        cameras: ["overview"],
        positions: { cube: [0.1, 0.2, 0.3] },
        error: null,
      }),
    }),
  );

  await page.goto("/");
  const panel = page.locator(".studio-live-panel");
  await expect(panel).toContainText("已连接");
  await expect(panel).toContainText("42");
  await expect(panel).toContainText("1.250 s");
  await expect(panel).toContainText("0.100, 0.200, 0.300");
  await expect(panel.getByRole("link", { name: /打开 RSS 原生 Viewer/ })).toHaveAttribute(
    "href",
    "http://127.0.0.1:8080/",
  );
});

test("workspace overview renders a connected RSS live session", async ({ page, request }) => {
  const response = await request.get(`${prefix}/studio/live`);
  test.skip(!response.ok(), "the target Workbench server does not expose the RSS live bridge");
  const status = await response.json();
  test.skip(!status.connected, "no configured live RSS session is available on the target server");

  await page.goto("/");
  const panel = page.locator(".studio-live-panel");
  await expect(panel).toContainText("已连接");
  await expect(panel).toContainText(String(status.step));
  await expect(panel).toContainText(status.paused ? "已暂停" : "运行中");
  await expect(panel).toContainText(status.names[0]);
});

test("workspace overview restores the scene and editor from an active session", async ({
  page,
  request,
}) => {
  const scenes = await (await request.get(`${prefix}/scenes`)).json();
  const scene = scenes.items.find((item: { issue: string | null }) => !item.issue);
  test.skip(!scene, "no registered native scene is available");
  const editorId = "0123456789abcdef01234567";
  await page.route(`**${prefix}/editors`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: editorId,
          scene_id: scene.id,
          source_sha256: "test",
          status: "ready",
          message: "ready",
          url: null,
          source_changed: false,
          revisions: [],
        },
      ]),
    }),
  );

  await page.goto("/");
  const resume = page.getByRole("link", { name: /继续场景编辑/ });
  await expect(resume).toContainText(scene.name);
  await expect(resume).toHaveAttribute(
    "href",
    `/scenes?scene=${scene.id}&editor=${editorId}`,
  );
});

test("real reports, video playback, evidence and refresh preserve the run context", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const result = await request.get(`${prefix}/runs?q=${standard}`);
  const listing = await result.json();
  expect(listing.total).toBe(1);
  const run = listing.items[0];
  await page.goto("/projects");
  await page.getByLabel("搜索运行", { exact: true }).fill(standard);
  await page
    .getByRole("link", { name: new RegExp(standard) })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: standard, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("单次全流程：未完成", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("跨调用证据：完整", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("发布资格：未确认", { exact: true }),
  ).toBeVisible();
  const video = page.locator("video");
  await expect(video).toBeVisible();
  await expect
    .poll(() => video.evaluate((node: HTMLVideoElement) => node.readyState))
    .toBeGreaterThanOrEqual(1);
  await video.evaluate((node: HTMLVideoElement) => node.play());
  await expect
    .poll(() => video.evaluate((node: HTMLVideoElement) => node.currentTime))
    .toBeGreaterThan(0.1);
  await video.evaluate((node: HTMLVideoElement) => node.pause());
  await page.getByRole("button", { name: /场景重建/ }).click();
  await page.getByRole("tab", { name: "验收", exact: true }).click();
  await expect(
    page.locator(".report-view .badge.warning").first(),
  ).toBeVisible();
  await expect(
    page.getByText("stage2_gate_suite", { exact: true }),
  ).toBeVisible();
  await page.getByText("stage2_gate_suite", { exact: true }).click();
  await expect(
    page
      .getByText(/NO SAMPLED POSE WITNESS|asset_path_portability_check/)
      .first(),
  ).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(
    new RegExp(`/runs/${run.id}\\?stage=stage2&view=validation`),
  );
  await expect(
    page.getByText("stage2_gate_suite", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "打开原始报告 ↗" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator(".text-preview")).toContainText(
    "dexagent.step_report.v1",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a real failed backend keeps its failed goal visible", async ({
  page,
  request,
}) => {
  const listing = await (
    await request.get(`${prefix}/runs?q=${failed}`)
  ).json();
  expect(listing.total).toBe(1);
  await page.goto(`/runs/${listing.items[0].id}?view=validation`);
  await expect(page.getByRole("heading", { name: failed })).toBeVisible();
  await expect(
    page.locator(".report-view .badge.failed").first(),
  ).toBeVisible();
  await expect(
    page.getByText(/subgoal 1 failed; downstream motion blocked/),
  ).toBeVisible();
});

test("local tools can be filtered and opened", async ({ page }) => {
  await page.goto("/tools");
  await page.getByLabel("搜索工具").fill("stage1_goal_gate");
  const target = page
    .locator(".tool-card")
    .filter({ has: page.getByText("stage1_goal_gate", { exact: true }) });
  await expect(target).toHaveCount(1);
  await target.click();
  await expect(page.getByRole("dialog", { name: "工具详情" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "输入", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
});

test("mobile workbench has usable navigation without horizontal page overflow", async ({
  page,
  request,
}) => {
  const listing = await (
    await request.get(`${prefix}/runs?q=${standard}`)
  ).json();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/runs/${listing.items[0].id}`);
  await expect(page.getByRole("heading", { name: standard })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "工作台", exact: true }),
  ).toHaveClass(/active/);
  expect(
    await page
      .getByRole("heading", { name: standard })
      .evaluate((node) => getComputedStyle(node).whiteSpace),
  ).toBe("normal");
  await page.getByRole("tab", { name: "验收", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "检查与证据", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "归属项目", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "项目", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});

test("workbench tabs support keyboard navigation and readable scope labels", async ({
  page,
  request,
}) => {
  const listing = await (
    await request.get(`${prefix}/runs?q=${standard}`)
  ).json();
  await page.goto(`/runs/${listing.items[0].id}`);
  const overview = page.getByRole("tab", { name: "概览", exact: true });
  await overview.focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    page.getByRole("tab", { name: "场景", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("End");
  await expect(
    page.getByRole("tab", { name: "产物", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    await page
      .locator(".scope-item")
      .first()
      .evaluate((node) => parseFloat(getComputedStyle(node).fontSize)),
  ).toBeGreaterThanOrEqual(12);
});

test("native episode charts read the real simulation clock", async ({
  page,
  request,
}) => {
  const listing = await (
    await request.get(`${prefix}/runs?q=rss_reference_cpu_final_20261016`)
  ).json();
  expect(listing.total).toBe(1);
  await page.goto(`/runs/${listing.items[0].id}?view=trajectory`);
  await page.getByRole("button", { name: "查看轨迹", exact: true }).click();
  await expect(page.getByRole("img", { name: /joint_q 通道 0/ })).toBeVisible();
  await expect(page.locator(".series-panel")).toContainText("3925");
  await expect(page.locator(".series-chart canvas")).toBeVisible();
  await page.getByLabel("轨迹字段", { exact: true }).selectOption("action");
  await expect(page.locator(".series-chart canvas")).toBeVisible();
});

test("the dataset page filters native episodes across runs and returns to source context", async ({
  page,
  request,
}) => {
  const response = await request.get(`${prefix}/episode-catalog?limit=100`);
  expect(response.ok()).toBeTruthy();
  const catalog = await response.json();
  test.skip(!catalog.total, "no native EpisodeStore records are available");
  const episode = catalog.items[0];

  await page.goto("/datasets");
  await expect(
    page.getByRole("heading", { name: "数据与回放", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".dataset-summary")).toContainText(
    `${catalog.total} 条原生 Episode 记录`,
  );

  await page.getByLabel("搜索数据记录").fill(episode.run_name);
  await page.getByRole("button", { name: "筛选", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/datasets\\?.*q=${encodeURIComponent(episode.run_name)}`));
  await page.getByLabel("按项目筛选").selectOption(episode.project_id);
  await page.getByLabel("按 Episode 结果筛选").selectOption(
    episode.success === null ? "unknown" : episode.success ? "passed" : "failed",
  );
  await page.getByLabel("按元信息完整度筛选").selectOption(
    episode.missing_fields.length || episode.issues.length ? "incomplete" : "complete",
  );

  const card = page
    .locator(".episode-card")
    .filter({ hasText: `EPISODE ${episode.name}` })
    .first();
  await expect(card).toContainText(episode.run_name);
  const source = card.getByRole("link", { name: "查看来源运行 ↗" });
  await expect(source).toHaveAttribute(
    "href",
    `/runs/${episode.run_id}?view=trajectory`,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await expect(page.getByLabel("按 Episode 结果筛选")).toBeVisible();
  await source.click();
  await expect(page).toHaveURL(new RegExp(`/runs/${episode.run_id}\\?view=trajectory`));
});

test("DexAgent curves retain native coordinate and control dimensions", async ({
  page,
  request,
}) => {
  const listing = await (
    await request.get(`${prefix}/runs?q=${standard}`)
  ).json();
  await page.goto(`/runs/${listing.items[0].id}?view=trajectory`);
  await expect(
    page.getByRole("heading", { name: "原生机器人轨迹" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看轨迹", exact: true }).click();
  await expect(page.getByRole("img", { name: /qpos 通道 0/ })).toBeVisible();
  await expect(page.locator(".series-panel")).toContainText("532");
  await expect(page.getByLabel("轨迹通道", { exact: true })).toHaveAttribute(
    "max",
    "69",
  );
  await page.getByLabel("轨迹字段", { exact: true }).selectOption("qvel");
  await expect(page.getByLabel("轨迹通道", { exact: true })).toHaveAttribute(
    "max",
    "67",
  );
  await page.getByLabel("轨迹字段", { exact: true }).selectOption("ctrl");
  await expect(page.getByLabel("轨迹通道", { exact: true })).toHaveAttribute(
    "max",
    "55",
  );
  await expect(page.locator(".series-chart canvas")).toBeVisible();
});

test("recorded rigid scene can be explored in the native 3D viewer", async ({
  page,
  request,
}) => {
  test.slow();
  const listing = await (
    await request.get(`${prefix}/runs?q=rss_reference_cpu_final_20261016`)
  ).json();
  expect(listing.total).toBe(1);
  await page.goto(`/runs/${listing.items[0].id}?view=scene`);
  await page.getByRole("button", { name: "生成 3D 回放", exact: true }).click();
  const frame = page.frameLocator('iframe[title="原生 3D 记录回放"]');
  await expect(
    frame.locator('canvas[data-engine^="three.js"]').first(),
  ).toBeVisible({
    timeout: 120_000,
  });
  await expect(page.locator(".native-scene")).toContainText("模型身份已核对");
  await expect(page.locator(".native-scene")).toContainText("原始 3925 帧");
  await expect(page).toHaveURL(/preview=[a-f0-9]{24}/);
  const canvas = frame.locator('canvas[data-engine^="three.js"]').first();
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  await canvas.hover();
  await page.mouse.wheel(0, -150);
  await page.reload();
  await expect(
    frame.locator('canvas[data-engine^="three.js"]').first(),
  ).toBeVisible({
    timeout: 30_000,
  });
});

test("the standard DexAgent trajectory has its own native 3D display", async ({
  page,
  request,
}) => {
  test.slow();
  const listing = await (
    await request.get(`${prefix}/runs?q=${standard}`)
  ).json();
  await page.goto(`/runs/${listing.items[0].id}?view=scene`);
  await page.getByRole("button", { name: "生成 3D 回放", exact: true }).click();
  const frame = page.frameLocator('iframe[title="原生 3D 记录回放"]');
  await expect(
    frame.locator('canvas[data-engine^="three.js"]').first(),
  ).toBeVisible({ timeout: 120_000 });
  await expect(page.locator(".native-scene")).toContainText("原始 532 帧");
  await expect(page.locator(".native-scene")).toContainText(
    "DexAgent 已记录关节姿态",
  );
  await expect(page.locator(".native-scene")).toContainText("未执行物理");
});

test("a preview from another run cannot appear under the current run header", async ({
  page,
  request,
}) => {
  const rss = await (
    await request.get(`${prefix}/runs?q=rss_reference_cpu_final_20261016`)
  ).json();
  const source = await (
    await request.post(`${prefix}/runs/${rss.items[0].id}/preview`, {
      data: {},
    })
  ).json();
  const current = await (
    await request.get(`${prefix}/runs?q=${standard}`)
  ).json();
  await page.goto(
    `/runs/${current.items[0].id}?view=scene&preview=${source.id}`,
  );
  await expect(
    page.getByRole("alert").filter({ hasText: "此预览属于另一条运行" }),
  ).toBeVisible();
  await expect(page.locator('iframe[title="原生 3D 记录回放"]')).toHaveCount(0);
});
