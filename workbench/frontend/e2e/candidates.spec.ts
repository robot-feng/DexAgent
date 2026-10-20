import { expect, test } from "@playwright/test";

const prefix = "/api/workbench/v1";

test("candidate controls bind offsets to the inspected version and block invalid sources", async ({
  page,
}) => {
  const config = "a".repeat(24),
    source = "b".repeat(24),
    invalid = "c".repeat(24);
  const sha = "d".repeat(64);
  let submitted: unknown;
  await page.route(`**${prefix}/executions/catalog`, (route) =>
    route.fulfill({
      json: {
        available: true,
        configs: [{ id: config, name: "fixture.yaml" }],
        modes: ["candidate"],
        detail: "UI contract only",
      },
    }),
  );
  await page.route(`**${prefix}/executions`, async (route) => {
    if (route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 422,
        json: { detail: "UI fixture; physics not executed" },
      });
    } else await route.fulfill({ json: [] });
  });
  await page.route(`**${prefix}/runs?limit=200`, (route) =>
    route.fulfill({
      json: {
        items: [
          { id: source, name: "Supported fixture" },
          { id: invalid, name: "Unsupported fixture" },
        ],
        total: 2,
      },
    }),
  );
  await page.route(`**${prefix}/executions/scenes/${source}?*`, (route) =>
    route.fulfill({
      json: {
        sha256: sha,
        objects: [
          {
            name: "cup",
            joint: "cup_free",
            position_m: [0.1, 0.2, 0],
            quaternion_wxyz: [1, 0, 0, 0],
          },
        ],
      },
    }),
  );
  await page.route(`**${prefix}/executions/scenes/${invalid}?*`, (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "该运行没有完整的 MJCF 场景" },
    }),
  );
  await page.goto(`/executions?mode=candidate&source=${source}`);
  await page.getByLabel("cup X 平移（米）").fill("0.01");
  await page.getByRole("button", { name: "启动运行", exact: true }).click();
  await expect(
    page.getByText("UI fixture; physics not executed"),
  ).toBeVisible();
  expect(submitted).toMatchObject({
    mode: "candidate",
    config_id: config,
    reference_run_id: source,
    scene_sha256: sha,
    scene_offsets: [{ name: "cup", translation_m: [0.01, 0, 0] }],
  });
  await page.getByLabel("参考运行").selectOption(invalid);
  await expect(page.getByText("该运行没有完整的 MJCF 场景")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "启动运行", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("cup X 平移（米）")).toHaveCount(0);
});

test("native candidate runs physics, retries the frozen version and opens recorded 3D", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.WORKBENCH_NATIVE_EXECUTION_TEST !== "1",
    "Requires the local DexAgent/RSS environment and source run.",
  );
  test.setTimeout(300_000);
  const catalog = await (
    await request.get(`${prefix}/executions/catalog`)
  ).json();
  const config = catalog.configs.find(
    (c: { name: string }) => c.name === "dexagent.yaml",
  );
  const runs = await (await request.get(`${prefix}/runs?limit=200`)).json();
  const source = runs.items.find(
    (r: { name: string }) => r.name === "e2e_top_smoke_20261013",
  );
  expect(config).toBeTruthy();
  expect(source).toBeTruthy();
  const owned: string[] = [];
  try {
    await page.goto(`/executions?mode=candidate&source=${source.id}`);
    await page.getByLabel("运行配置").selectOption(config.id);
    await page.getByLabel("cup_cup X 平移（米）").fill("0.01");
    const launched = page.waitForResponse(
      (r) =>
        r.url().endsWith(`${prefix}/executions`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "启动运行", exact: true }).click();
    const response = await launched;
    expect(response.status()).toBe(202);
    const first = await response.json();
    owned.push(first.id);
    await expect
      .poll(
        async () =>
          (await (await request.get(`${prefix}/executions/${first.id}`)).json())
            .status,
        { timeout: 130_000 },
      )
      .toBe("completed");
    const done = await (
      await request.get(`${prefix}/executions/${first.id}`)
    ).json();
    expect(done.candidate_sha256).toMatch(/^[a-f0-9]{64}$/);
    const run = await (
      await request.get(`${prefix}/runs/${done.run_id}`)
    ).json();
    expect(run.validation).toBe("passed");
    expect(run.release).toBe("not_evaluated");
    expect(run.recorded_3d_available).toBe(true);
    await expect(
      page.getByText(done.candidate_sha256, { exact: true }),
    ).toBeVisible();
    const retried = page.waitForResponse(
      (r) =>
        r.url().endsWith(`${prefix}/executions/${first.id}/retry`) &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "以新运行重试", exact: true })
      .click();
    const retryResponse = await retried;
    expect(retryResponse.status()).toBe(202);
    const retry = await retryResponse.json();
    owned.push(retry.id);
    expect(retry.output_name).not.toBe(first.output_name);
    await expect
      .poll(
        async () =>
          (await (await request.get(`${prefix}/executions/${retry.id}`)).json())
            .status,
        { timeout: 130_000 },
      )
      .toBe("completed");
    const retryDone = await (
      await request.get(`${prefix}/executions/${retry.id}`)
    ).json();
    expect(retryDone.candidate_sha256).toBe(done.candidate_sha256);
    await page.goto(`/runs/${done.run_id}?view=scene`);
    await page
      .getByRole("button", { name: "生成 3D 回放", exact: true })
      .click();
    await expect(page.getByTitle("原生 3D 记录回放")).toBeVisible({
      timeout: 100_000,
    });
    await expect(page.getByText(/模型身份已核对 · 未执行物理/)).toBeVisible();
    await expect(
      page.getByRole("link", { name: "调整物体布局" }),
    ).toBeVisible();
  } finally {
    for (const id of owned) {
      const state = await (
        await request.get(`${prefix}/executions/${id}`)
      ).json();
      if (state.status === "running")
        await request.post(`${prefix}/executions/${id}/stop`);
    }
  }
});
