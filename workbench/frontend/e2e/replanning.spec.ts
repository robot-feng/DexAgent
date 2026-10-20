import { expect, test } from "@playwright/test";

const prefix = "/api/workbench/v1";

test("replanning binds the accepted layout and source task before launching", async ({
  page,
}) => {
  const config = "a".repeat(24),
    source = "b".repeat(24),
    invalid = "c".repeat(24),
    sha = "d".repeat(64);
  let submitted: unknown;
  await page.route(`**${prefix}/executions/catalog`, (route) =>
    route.fulfill({
      json: {
        available: true,
        configs: [{ id: config, name: "fixture.yaml" }],
        modes: ["pipeline", "replan"],
        detail: "UI contract only",
      },
    }),
  );
  await page.route(`**${prefix}/executions`, (route) => {
    if (route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      return route.fulfill({
        status: 422,
        json: { detail: "UI contract fixture; planner not executed" },
      });
    }
    return route.fulfill({ json: [] });
  });
  await page.route(`**${prefix}/runs?limit=200`, (route) =>
    route.fulfill({
      json: {
        items: [
          { id: source, name: "Accepted candidate" },
          { id: invalid, name: "Missing source evidence" },
        ],
        total: 2,
      },
    }),
  );
  await page.route(`**${prefix}/executions/tasks/${source}`, (route) =>
    route.fulfill({
      json: {
        sha256: sha,
        candidate_sha256: "e".repeat(64),
        source_run_id: "f".repeat(24),
        task: "The cup rests inside the bowl.",
      },
    }),
  );
  await page.route(`**${prefix}/executions/tasks/${invalid}`, (route) =>
    route.fulfill({ status: 422, json: { detail: "源任务缺少独立来源凭据" } }),
  );
  await page.goto(`/executions?mode=replan&source=${source}`);
  await expect(
    page.getByText("The cup rests inside the bowl."),
  ).toBeVisible();
  await expect(page.getByLabel("执行到")).toHaveValue("stage3");
  await page.getByRole("button", { name: "启动运行", exact: true }).click();
  await expect(
    page.getByText("UI contract fixture; planner not executed"),
  ).toBeVisible();
  expect(submitted).toMatchObject({
    mode: "replan",
    profile: "pipeline_eval",
    stop_at: "stage3",
    reference_run_id: source,
    task_inputs_sha256: sha,
    video: "",
    prompt: "",
  });
  await page.getByLabel("参考运行").selectOption(invalid);
  await expect(page.getByText("源任务缺少独立来源凭据")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "启动运行", exact: true }),
  ).toBeDisabled();
});
