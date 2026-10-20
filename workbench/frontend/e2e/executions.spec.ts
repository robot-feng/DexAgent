import { expect, test } from "@playwright/test";

const prefix = "/api/workbench/v1";

test("waiting for an Agent is visible and clears when a response arrives", async ({
  page,
}) => {
  let waiting = true;
  const job = {
    id: "e".repeat(24),
    mode: "pipeline",
    config_name: "dexagent.yaml",
    profile: "pipeline_eval",
    status: "running",
    message: "等待 Agent 响应",
    output_name: "UI contract fixture",
    run_id: null,
    returncode: null,
    created_at: "2026-10-20T02:00:00Z",
    finished_at: null,
    retry_of: null,
  };
  await page.route(`**${prefix}/executions`, (route) =>
    route.fulfill({
      json: [
        {
          ...job,
          agent_wait: waiting
            ? {
                request_id: "session-0001",
                turn: 1,
                request_path:
                  "/fixture/run/external/requests/session-0001.json",
              }
            : null,
        },
      ],
    }),
  );
  await page.route(`**${prefix}/executions/${job.id}/log`, (route) =>
    route.fulfill({ json: { text: "UI contract fixture only" } }),
  );
  await page.goto("/executions");
  await expect(page.getByText("等待 Agent 处理第 1 轮请求")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "复制 Agent 请求路径" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "启动运行", exact: true }),
  ).toBeDisabled();
  waiting = false;
  await expect(page.getByText("等待 Agent 处理第 1 轮请求")).toHaveCount(0);
  await page.goto("/executions?job=" + "f".repeat(24));
  await expect(page.getByText("该运行记录不存在。")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "停止运行", exact: true }),
  ).toHaveCount(0);
});

test.describe("native CLI execution", () => {
  test.skip(
    process.env.WORKBENCH_NATIVE_EXECUTION_TEST !== "1",
    "Explicitly opt in on a machine with the RSS environment.",
  );
  test.describe.configure({ mode: "serial", timeout: 120_000 });
  const owned: string[] = [];

  test.afterEach(async ({ request }) => {
    for (const id of owned.splice(0)) {
      const job = await (
        await request.get(`${prefix}/executions/${id}`)
      ).json();
      if (job.status === "running")
        await request.post(`${prefix}/executions/${id}/stop`);
    }
  });

  test("launches RSS rigid physics and opens its native validation", async ({
    page,
    request,
  }) => {
    const catalog = await (
      await request.get(`${prefix}/executions/catalog`)
    ).json();
    const config = catalog.configs.find(
      (value: { name: string }) => value.name === "dexagent.yaml",
    );
    expect(config).toBeTruthy();
    await page.goto("/executions");
    await page.getByLabel("任务类型").selectOption("rigid");
    await page.getByLabel("运行配置").selectOption(config.id);
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith(`${prefix}/executions`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "启动运行", exact: true }).click();
    const launch = await response;
    expect(launch.status()).toBe(202);
    const job = await launch.json();
    owned.push(job.id);
    await expect
      .poll(
        async () =>
          (await (await request.get(`${prefix}/executions/${job.id}`)).json())
            .status,
        { timeout: 110_000 },
      )
      .toBe("completed");
    const done = await (
      await request.get(`${prefix}/executions/${job.id}`)
    ).json();
    expect(done.returncode).toBe(0);
    const run = await (
      await request.get(`${prefix}/runs/${done.run_id}`)
    ).json();
    expect(run.scope.single_invocation_full_pipeline_complete).toBe(false);
    expect(run.release).toBe("not_evaluated");
    const checks = run.stages.flatMap(
      (stage: {
        report?: {
          checks: { id: string; required: boolean; status: string }[];
        };
      }) => stage.report?.checks ?? [],
    );
    expect(checks.length).toBeGreaterThanOrEqual(8);
    expect(
      checks
        .filter((c: { required: boolean }) => c.required)
        .every((c: { status: string }) => c.status === "passed"),
    ).toBe(true);
    expect(
      checks.find((c: { id: string }) => c.id === "rendered_video")?.status,
    ).toBe("passed");
    await expect(page.getByLabel("原生验收结果")).toContainText("必需检查 9/9");
    await expect(page.getByLabel("原生验收结果")).toContainText(
      "发布资格未确认",
    );
    await page
      .getByRole("link", { name: "查看运行与验收", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`/runs/${done.run_id}`));
    await expect(
      page.getByRole("heading", { name: job.output_name }),
    ).toBeVisible();
  });

  test("stops a native launch and retries into a different run", async ({
    page,
  }) => {
    await page.goto("/executions");
    await page.getByLabel("任务类型").selectOption("grasp");
    await page.getByLabel("跳过离线视频渲染").check();
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith(`${prefix}/executions`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "启动运行", exact: true }).click();
    const job = await (await response).json();
    owned.push(job.id);
    const stopped = page.waitForResponse((r) =>
      r.url().endsWith(`/executions/${job.id}/stop`),
    );
    await page.getByRole("button", { name: "停止运行", exact: true }).click();
    expect((await (await stopped).json()).status).toBe("stopped");
    const retried = page.waitForResponse((r) =>
      r.url().endsWith(`/executions/${job.id}/retry`),
    );
    await page
      .getByRole("button", { name: "以新运行重试", exact: true })
      .click();
    const retry = await (await retried).json();
    owned.push(retry.id);
    expect(retry.retry_of).toBe(job.id);
    expect(retry.output_name).not.toBe(job.output_name);
    const stopRetry = page.waitForResponse((r) =>
      r.url().endsWith(`/executions/${retry.id}/stop`),
    );
    await page.getByRole("button", { name: "停止运行", exact: true }).click();
    expect((await (await stopRetry).json()).status).toBe("stopped");
    await expect(
      page.getByText("本次重试使用原配置快照，并写入新运行目录。"),
    ).toBeVisible();
  });
});
