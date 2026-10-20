import { expect, test, type Page } from "@playwright/test";

const prefix = "/api/workbench/v1";
const configA = "a".repeat(24);
const configB = "b".repeat(24);
const modes = ["pipeline", "simfoundry", "reference", "candidate", "replan", "grasp", "sharpa", "rigid", "towel"];
const runtime = (config = configA) => ({
  config_id: config,
  config_name: config === configA ? "fixture-rss.yaml" : "fixture-missing.yaml",
  engine: "Newton",
  integration: "RoboSimStudio",
  python: "/fixture/environment/bin/python",
  status: config === configA ? "environment_detected" : "missing_dependencies",
  checked_at: "2026-10-20T10:00:00Z",
  detail: config === configA ? "仅用于前端契约测试的环境信息。" : "该配置未安装 newton。",
  packages: [{ name: "newton", version: config === configA ? "1.6.0" : null, available: config === configA }],
  profiles: [
    { mode: "rigid", solver: "SolverMuJoCo", physics_device: "cpu", detail: "刚体基础检查通过 Newton 的 MuJoCo 求解器执行。" },
    { mode: "grasp", solver: "SolverMuJoCo", physics_device: "cpu", detail: "抓取物理在 CPU 执行，渲染设备另行配置。" },
    { mode: "towel", solver: "SolverMuJoCo + SolverVBD", physics_device: "cuda:0", detail: "毛巾示例耦合刚体与布料求解。" },
  ],
});

async function mockWorkspace(page: Page) {
  await page.route(`**${prefix}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname.slice(prefix.length);
    const data: Record<string, unknown> = {
      "/health": { status: "ok", run_count: 0 },
      "/scenes": { items: [], total: 0 },
      "/editors": [],
      "/capabilities": [],
      "/studio/live": { connected: false, configured: false, url: null },
      "/runs": { items: [], total: 0 },
      "/executions": [],
      "/executions/catalog": {
        available: true,
        configs: [{ id: configA, name: "fixture-rss.yaml" }, { id: configB, name: "fixture-missing.yaml" }],
        modes,
        detail: "UI fixture only",
      },
    };
    if (path === "/simulation/runtime") {
      const config = new URL(route.request().url()).searchParams.get("config_id");
      return route.fulfill({ json: runtime(config ?? configA) });
    }
    await route.fulfill({ json: data[path] ?? {} });
  });
}

test.beforeEach(async ({ page }) => mockWorkspace(page));

test("overview shows detected runtime and opens the rigid mode", async ({ page }) => {
  await page.goto("/");
  const card = page.getByRole("region", { name: "仿真运行环境" });
  await expect(card).toContainText("Newton · RoboSimStudio");
  await expect(card).toContainText("环境已检测");
  await expect(card).toContainText("环境检测不等于任务通过");
  await card.getByText("环境版本与检测时间").click();
  await expect(card).toContainText("1.6.0");
  await card.getByRole("link", { name: "运行刚体基础验收" }).click();
  await expect(page.getByLabel("任务类型")).toHaveValue("rigid");
  await expect(page.getByLabel("所选任务仿真路径")).toContainText("SolverMuJoCo");
});

test("runtime follows the selected configuration and preserves CPU physics semantics", async ({ page }) => {
  await page.goto("/executions?mode=grasp");
  const card = page.getByRole("region", { name: "仿真运行环境" });
  await expect(page.getByLabel("任务类型")).toHaveValue("grasp");
  await expect(card).toContainText("物理计算：CPU");
  await expect(card).not.toContainText("CUDA");
  const requested = page.waitForRequest((request) =>
    request.url().includes(`/simulation/runtime?config_id=${configB}`),
  );
  await page.getByLabel("运行配置").selectOption(configB);
  await requested;
  await expect(card).toContainText("fixture-missing.yaml");
  await expect(card).toContainText("缺少依赖");
  await expect(card).not.toContainText("环境已检测");
  await page.getByLabel("任务类型").selectOption("pipeline");
  await expect(card.getByLabel("所选任务仿真路径")).toContainText("多阶段流程");
  await expect(card.getByLabel("所选任务仿真路径")).not.toContainText("SolverMuJoCo");
  await page.getByLabel("任务类型").selectOption("replan");
  await expect(card.getByLabel("所选任务仿真路径")).toContainText("多阶段流程");
});

test("loading and fetch failure are visible, and retry can recover", async ({ page }) => {
  let complete: (() => void) | undefined;
  const held = new Promise<void>((resolve) => { complete = resolve; });
  let fail = true;
  await page.route(`**${prefix}/simulation/runtime*`, async (route) => {
    await held;
    await route.fulfill(
      fail ? { status: 503, json: { detail: "仿真环境探测暂不可用" } } : { json: runtime() },
    );
  });
  await page.goto("/executions?mode=rigid");
  const card = page.getByRole("region", { name: "仿真运行环境" });
  await expect(card.getByRole("status")).toContainText("正在检测");
  complete!();
  await expect(card.getByRole("alert")).toContainText("仿真环境探测暂不可用");
  await expect(card).not.toContainText("环境已检测");
  fail = false;
  await card.getByRole("button", { name: "重新检测仿真运行环境" }).click();
  await expect(card).toContainText("环境已检测");
});

test("invalid query mode safely falls back and runtime fits narrow screens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/executions?mode=__proto__");
  await expect(page.getByLabel("任务类型")).toHaveValue("pipeline");
  const card = page.getByRole("region", { name: "仿真运行环境" });
  await expect(card).toContainText("多阶段流程");
  await card.getByText("环境版本与检测时间").click();
  const bounds = await card.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(await card.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
});
